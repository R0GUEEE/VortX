#!/usr/bin/env bash
# Focused source contracts and executable dirty-state fixtures for the pinned MPVKit-DVFEL
# builder and release artifact gate. No native artifact build, download or publication runs;
# source verification compiles a bounded production Swift helper.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/scripts/build-mpvkit-dvfel.sh"
PATCH="$ROOT/scripts/mpvkit-dvfel.patch"
VERIFY="$ROOT/scripts/verify-mpvkit-dvfel-artifacts.sh"
WORKFLOW="$ROOT/.github/workflows/release-tvos.yml"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

for file in "$BUILD" "$PATCH" "$VERIFY" "$WORKFLOW"; do
  [ -f "$file" ] || fail "missing required source: $file"
done

grep -Eq '^MPVKIT_REF="[0-9a-f]{40}"' "$BUILD" || fail "builder must retain one full MPVKit source pin"
grep -Fq 'MPVKIT_DIR="${MPVKIT_DVFEL_WORK:-$REPO/../MPVKit}"' "$BUILD" ||
  fail "builder default must use the canonical MPVKit sibling checkout"
grep -Fq 'DEST="${MPVKIT_DVFEL_DEST:-$REPO/_build/MPVKit-DVFEL}"' "$BUILD" ||
  fail "builder must stage by default in canonical _build"
if grep -Fq '$HOME/.cache/vortx-mpvkit-dvfel' "$BUILD"; then
  fail "builder must not silently use a HOME cache as its default source"
fi
if grep -Fq 'git checkout --quiet -- .' "$BUILD"; then
  fail "builder must not reset tracked user changes"
fi
grep -Fq 'git -C "$MPVKIT_DIR" rev-parse HEAD' "$BUILD" || fail "builder must validate an existing checkout HEAD"
grep -Fq 'FRESH_HEAD' "$BUILD" || fail "builder must assert the fresh clone resolved to the exact pin"
grep -Fq 'matches_exact_patch_tree' "$BUILD" || fail "builder must prove the complete approved patch tree"
grep -Fq 'exact MPVKit patch already applied' "$BUILD" || fail "builder must support an explicit exact-patch retry"
grep -Fq 'existing generated patch differs' "$BUILD" || fail "builder must not overwrite a changed generated patch"
grep -Fq 'diff --git a/Sources/BuildScripts/patch/libmpv/0002-revert-build-static.patch b/Sources/BuildScripts/patch/libmpv/0002-revert-build-static.patch' "$PATCH" ||
  fail "approved MPVKit tree must carry the exact Swift frontend recipe correction"
grep -Fq "+    swift_flags = ['-frontend', '-c', '-whole-module-optimization', '-sdk', macos_sdk_path," "$PATCH" ||
  fail "one-object Swift frontend recipe must request whole-module compilation"

# The Swift patch must request command output explicitly: Utility.shell returns an empty string on
# success otherwise, which would make a correct rev/status look absent. It must also reject arbitrary
# dirty state instead of treating a reverse hunk check as a full-tree proof.
grep -Fq 'Utility.shell(command, isOutput: true, currentDirectoryURL: directoryURL)' "$PATCH" ||
  fail "SpikeGit output/success checks must request shell output explicitly"
grep -Fq 'matchesExactPatchTree' "$PATCH" || fail "SpikeGit must compare the complete tree to the approved patch set"
grep -Fq 'git status --porcelain --untracked-files=all' "$PATCH" || fail "SpikeGit must inspect dirty state"
if grep -Fq 'test -z \\\"$(git status --porcelain --untracked-files=all)\\\"' "$PATCH"; then
  fail "SpikeGit must not require an exact applied checkout to have an empty status"
fi
grep -Fq 'git diff --cached --quiet' "$PATCH" || fail "SpikeGit must reject staged changes"
grep -Fq 'git rev-parse HEAD' "$PATCH" || fail "SpikeGit must validate an existing HEAD"
grep -Fq 'expected exact pin' "$PATCH" || fail "SpikeGit must assert the fresh checkout pin"
grep -Fq 'git apply --reverse --check' "$PATCH" || fail "SpikeGit must support an exact already-applied patch"
grep -Fq 'patch neither applies cleanly nor is already applied' "$PATCH" || fail "SpikeGit must fail actionable patch conflicts"

# The artifact verifier must inspect content, not only checksums or mtimes.
grep -Fq 'LIBAVCODEC_VERSION_MAJOR' "$VERIFY" || fail "artifact gate must inspect libavcodec major"
grep -Fq 'LIBAVFORMAT_VERSION_MAJOR' "$VERIFY" || fail "artifact gate must inspect libavformat major"
grep -Fq 'LIBAVCODEC_VERSION_MAJOR[[:space:]]+63' "$VERIFY" || fail "artifact gate must require libavcodec major 63"
grep -Fq 'LIBAVFORMAT_VERSION_MAJOR[[:space:]]+63' "$VERIFY" || fail "artifact gate must require libavformat major 63"
grep -Fq 'verify_ffmpeg_maintenance "$target" "$headers" "$slice"' "$VERIFY" ||
  fail "artifact gate must reject the superseded 63.1.100 artifact in every FFmpeg slice"
grep -Fq '${prefix}_VERSION_MINOR[[:space:]]+1' "$VERIFY" || fail "artifact gate must require maintenance minor 1"
grep -Fq '${prefix}_VERSION_MICRO[[:space:]]+102' "$VERIFY" || fail "artifact gate must require maintenance micro 102"
grep -Fq 'ffmpegPinnedSHA = "946fcce07b6dcd0331c8cc609192aeff5e1924f8"' "$PATCH" ||
  fail "builder must bind the reviewed n9.0.2 commit"
grep -Fq 'ffmpegPinnedBranch = "n9.0.2"' "$PATCH" || fail "builder must fetch the reviewed release tag"
grep -Fq 'Libmpv-GPL; do' "$VERIFY" || fail "artifact gate must validate Libmpv slices"
grep -Fq 'expected_arches' "$VERIFY" || fail "artifact gate must require exact per-slice architectures"
grep -Fq 'tvos-arm64_arm64e' "$VERIFY" || fail "artifact gate must enumerate the tvOS arm64e slice"
grep -Fq 'dovi_split' "$VERIFY" || fail "artifact gate must require dovi_split"
grep -Fq 'if [ "$target" = Libavcodec-GPL ]; then' "$VERIFY" ||
  fail "artifact gate must scope dovi_split to libavcodec"
grep -Fq 'verify-mpvkit-dvfel-apple-tls.sh' "$VERIFY" || fail "artifact gate must retain TLS verification"

# Extract the real fetch step and require the verifier after unzip, before the next workflow step.
fetch_step="$(awk '/^[[:space:]]+- name: Fetch the MPVKit-DVFEL artifacts/{active=1; next}
  active && /^[[:space:]]+- name:/{exit} active{print}' "$WORKFLOW")"
grep -Fq 'unzip -oq /tmp/mpvkit.zip -d "$DEST"' <<<"$fetch_step" ||
  fail "release fetch step must unpack the pinned artifact"
grep -Fq 'bash scripts/verify-mpvkit-dvfel-artifacts.sh "$DEST"' <<<"$fetch_step" ||
  fail "release fetch step must run the real MPV artifact verifier"
unzip_line="$(grep -nF 'unzip -oq /tmp/mpvkit.zip -d "$DEST"' <<<"$fetch_step" | cut -d: -f1)"
verify_line="$(grep -nF 'bash scripts/verify-mpvkit-dvfel-artifacts.sh "$DEST"' <<<"$fetch_step" | cut -d: -f1)"
[ "$verify_line" -gt "$unzip_line" ] || fail "artifact verifier must run after unpack"

# Exercise the production builder preflight against a tiny local Git fixture. The approved patch is
# generated from the fixture's clean base, then the same builder is run for a clean apply, an exact
# patched retry including tracked 0002, unrelated/partial recipe edits, and an unknown untracked
# file. No native build tools are used: the builder exits only after checkout/patch preflight.
TEST_BUILD_ROOT="$ROOT/.build"
mkdir -p "$TEST_BUILD_ROOT"
FIXTURE="$(mktemp -d "$TEST_BUILD_ROOT/mpvkit-contract.XXXXXX")"
TOOLS="$FIXTURE/tools"
SOURCE="$FIXTURE/mpvkit"
FIXTURE_PATCH="$FIXTURE/approved.patch"
mkdir -p "$TOOLS" "$SOURCE"
cleanup_fixture() {
  rm -rf "$FIXTURE"
  rmdir "$TEST_BUILD_ROOT" 2>/dev/null || true
}
trap cleanup_fixture EXIT

# A verified component ZIP also contains root files such as .gitignore that already
# exist in the vendor directory. Both real consumers must extract without a stdin
# prompt; do not mistake an EOF prompt failure for bad native payload contents.
SHELL_WORKFLOW="$ROOT/.github/workflows/release-packaging-validation.yml"
grep -Fq 'unzip -oq "$RUNNER_TEMP/mpvkit.zip" -d "$DEST"' "$SHELL_WORKFLOW" ||
  fail "secretless consumer must noninteractively unpack the verified artifact"
mkdir -p "$FIXTURE/extract-source/artifacts" "$FIXTURE/extract-destination"
printf 'verified component root\n' > "$FIXTURE/extract-source/.gitignore"
printf 'verified payload\n' > "$FIXTURE/extract-source/artifacts/payload"
printf 'previous vendor root\n' > "$FIXTURE/extract-destination/.gitignore"
(
  cd "$FIXTURE/extract-source"
  zip -q "$FIXTURE/collision.zip" .gitignore artifacts/payload
)
unzip -oq "$FIXTURE/collision.zip" -d "$FIXTURE/extract-destination" < /dev/null
cmp "$FIXTURE/extract-source/.gitignore" "$FIXTURE/extract-destination/.gitignore" ||
  fail "noninteractive unpack did not replace the known existing component file"
cmp "$FIXTURE/extract-source/artifacts/payload" "$FIXTURE/extract-destination/artifacts/payload" ||
  fail "noninteractive unpack lost an artifact entry"

git -C "$SOURCE" init -q
git -C "$SOURCE" config user.name "MPVKit contract fixture"
git -C "$SOURCE" config user.email "fixture@example.invalid"
mkdir -p "$SOURCE/Sources"
printf '%s\n' 'let packageValue = "base"' >"$SOURCE/Package.swift"
printf '%s\n' 'let sourceValue = "base"' >"$SOURCE/Sources/main.swift"
FIXTURE_SWIFT_RECIPE='Sources/BuildScripts/patch/libmpv/0002-revert-build-static.patch'
mkdir -p "$(dirname "$SOURCE/$FIXTURE_SWIFT_RECIPE")"
printf '%s\n' "swift_flags = ['-frontend', '-c', '-sdk', 'fixture-sdk']" >"$SOURCE/$FIXTURE_SWIFT_RECIPE"
cp "$SOURCE/Package.swift" "$FIXTURE/base-package.swift"
cp "$SOURCE/$FIXTURE_SWIFT_RECIPE" "$FIXTURE/base-swift-recipe.patch"
git -C "$SOURCE" add -- Package.swift Sources/main.swift "$FIXTURE_SWIFT_RECIPE"
git -C "$SOURCE" commit -q -m "fixture base"
FIXTURE_REF="$(git -C "$SOURCE" rev-parse HEAD)"

printf '%s\n' 'let packageValue = "approved"' >"$SOURCE/Package.swift"
printf '%s\n' "swift_flags = ['-frontend', '-c', '-whole-module-optimization', '-sdk', 'fixture-sdk']" >"$SOURCE/$FIXTURE_SWIFT_RECIPE"
git -C "$SOURCE" diff --binary -- Package.swift "$FIXTURE_SWIFT_RECIPE" >"$FIXTURE_PATCH"
[ -s "$FIXTURE_PATCH" ] || fail "fixture patch was not generated"
cp "$FIXTURE/base-package.swift" "$SOURCE/Package.swift"
cp "$FIXTURE/base-swift-recipe.patch" "$SOURCE/$FIXTURE_SWIFT_RECIPE"
[ -z "$(git -C "$SOURCE" status --porcelain --untracked-files=all)" ] ||
  fail "fixture must start clean"

# Capture the installed resolver before creating the builder-only tool stubs. The
# target-isolation fixture must exercise real pkg-config metadata resolution.
PKG_CONFIG_EXECUTABLE="$(command -v pkg-config)" || fail "installed pkg-config is required"
case "$PKG_CONFIG_EXECUTABLE" in
  /*) [ -x "$PKG_CONFIG_EXECUTABLE" ] || fail "pkg-config is not executable" ;;
  *) fail "pkg-config must resolve to an absolute installed executable" ;;
esac

for tool in meson ninja cmake pkg-config nasm wget; do
  printf '%s\n' '#!/bin/sh' 'exit 0' >"$TOOLS/$tool"
  chmod +x "$TOOLS/$tool"
done

run_builder() {
  local log="$1"
  MPVKIT_DVFEL_WORK="$SOURCE" \
    MPVKIT_DVFEL_REF="$FIXTURE_REF" \
    MPVKIT_DVFEL_PATCH="$FIXTURE_PATCH" \
    MPVKIT_DVFEL_DEST="$FIXTURE/stage" \
    MPVKIT_DVFEL_PREFLIGHT_ONLY=1 \
    PATH="$TOOLS:$PATH" \
    "$BUILD" >"$log" 2>&1
}

run_builder "$FIXTURE/clean.log" || fail "clean fixture preflight failed: $(cat "$FIXTURE/clean.log")"
grep -Fq 'preflight-only checkout and patch validation passed' "$FIXTURE/clean.log" ||
  fail "clean fixture did not reach the preflight success path"
grep -Fq 'approved' "$SOURCE/Package.swift" || fail "clean fixture did not apply its approved patch"
grep -Fq -- '-whole-module-optimization' "$SOURCE/$FIXTURE_SWIFT_RECIPE" ||
  fail "clean fixture did not carry the approved Swift frontend recipe"
cp "$SOURCE/Package.swift" "$FIXTURE/exact-package.swift"
cp "$SOURCE/$FIXTURE_SWIFT_RECIPE" "$FIXTURE/exact-swift-recipe.patch"

run_builder "$FIXTURE/retry.log" || fail "exact patched retry failed: $(cat "$FIXTURE/retry.log")"
grep -Fq 'exact MPVKit patch already applied' "$FIXTURE/retry.log" ||
  fail "exact patched retry did not use the idempotent path"

printf '%s\n' 'let unrelated = true' >>"$SOURCE/Package.swift"
if run_builder "$FIXTURE/unrelated.log"; then
  fail "unrelated same-file edit was accepted"
fi
grep -Fq 'exact approved VortX patch' "$FIXTURE/unrelated.log" ||
  fail "unrelated same-file edit failed without the exact-tree diagnostic"
cp "$FIXTURE/exact-package.swift" "$SOURCE/Package.swift"

printf '%s\n' 'unrelated recipe edit' >>"$SOURCE/$FIXTURE_SWIFT_RECIPE"
if run_builder "$FIXTURE/unrelated-recipe.log"; then
  fail "unrelated edit to the approved tracked Swift recipe was accepted"
fi
grep -Fq 'exact approved VortX patch' "$FIXTURE/unrelated-recipe.log" ||
  fail "unrelated recipe edit failed without the exact-tree diagnostic"
cp "$FIXTURE/exact-swift-recipe.patch" "$SOURCE/$FIXTURE_SWIFT_RECIPE"

cp "$FIXTURE/base-swift-recipe.patch" "$SOURCE/$FIXTURE_SWIFT_RECIPE"
if run_builder "$FIXTURE/partial-recipe.log"; then
  fail "partial approved tree with the Swift recipe missing was accepted"
fi
grep -Fq 'exact approved VortX patch' "$FIXTURE/partial-recipe.log" ||
  fail "partial recipe failed without the exact-tree diagnostic"
cp "$FIXTURE/exact-swift-recipe.patch" "$SOURCE/$FIXTURE_SWIFT_RECIPE"

printf '%s\n' 'untracked fixture data' >"$SOURCE/untracked.txt"
if run_builder "$FIXTURE/untracked.log"; then
  fail "unknown untracked file was accepted"
fi
grep -Fq 'unexpected dirty state' "$FIXTURE/untracked.log" ||
  fail "unknown untracked file failed without the dirty-state diagnostic"
rm -f "$SOURCE/untracked.txt"

[ "$(git -C "$SOURCE" status --porcelain --untracked-files=all)" = "$(printf '%s\n' ' M Package.swift' " M $FIXTURE_SWIFT_RECIPE")" ] ||
  fail "fixture did not retain only the exact approved patch after dirty-state cases"

# Compile actual patched SpikeGit and target pkg-config getters extracted from canonical MPVKit.
# The fixture supplies surrounding types and a Process-backed Utility, retaining production bodies.
CANONICAL_MPVKIT="${MPVKIT_DVFEL_CANONICAL:-/Users/daksh/VortXTV/MPVKit}"
[ -d "$CANONICAL_MPVKIT/.git" ] || fail "canonical MPVKit checkout is unavailable: $CANONICAL_MPVKIT"
CANONICAL_REF="$(git -C "$CANONICAL_MPVKIT" rev-parse HEAD)"
[ "$CANONICAL_REF" = "2103893078c5e339073b11737b86f7f22b9c4491" ] ||
  fail "canonical MPVKit fixture source is not pinned to 2103893: $CANONICAL_REF"

PATCHED_INDEX="$FIXTURE/mpvkit-source.index"
PATCHED_SOURCE="$FIXTURE/mpvkit-main.swift"
PATCHED_BASE="$FIXTURE/mpvkit-base.swift"
# Verify the entire approved patch in an isolated index, then emit only these two inputs.
# No source checkout or canonical index/working-tree edit is created.
GIT_INDEX_FILE="$PATCHED_INDEX" git -C "$CANONICAL_MPVKIT" read-tree HEAD
GIT_INDEX_FILE="$PATCHED_INDEX" git -C "$CANONICAL_MPVKIT" apply --cached "$PATCH"
GIT_INDEX_FILE="$PATCHED_INDEX" git -C "$CANONICAL_MPVKIT" show :Sources/BuildScripts/XCFrameworkBuild/main.swift >"$PATCHED_SOURCE"
GIT_INDEX_FILE="$PATCHED_INDEX" git -C "$CANONICAL_MPVKIT" show :Sources/BuildScripts/XCFrameworkBuild/base.swift >"$PATCHED_BASE"
swiftc -j 1 -num-threads 1 -parse "$PATCHED_SOURCE" ||
  fail "actual patched MPVKit BuildScripts source did not parse"
SPIKE_BLOCK="$FIXTURE/spikegit-production.swift"
sed -n '/^enum SpikeGit {/,/^private class BuildOpenSSL: ZipBaseBuild {/p' \
  "$PATCHED_SOURCE" | sed '$d' >"$SPIKE_BLOCK"
[ -s "$SPIKE_BLOCK" ] || fail "could not extract the patched production SpikeGit implementation"
ENVIRONMENT_BLOCK="$FIXTURE/environment-production.swift"
PKG_CONFIG_BLOCK="$FIXTURE/pkgconfig-production.swift"
# These selectors end at the method's four-space closing brace, preserving each
# complete production body without pulling unrelated SDK/build implementation in.
sed -n '/^    func environment(platform: PlatformType, arch: ArchType) -> \[String: String\] {/,/^    }/p' \
  "$PATCHED_BASE" >"$ENVIRONMENT_BLOCK"
sed -n '/^    func pkgConfigPath(arch: ArchType) -> String {/,/^    }/p' \
  "$PATCHED_BASE" >"$PKG_CONFIG_BLOCK"
[ -s "$ENVIRONMENT_BLOCK" ] || fail "could not extract production BaseBuild.environment"
[ -s "$PKG_CONFIG_BLOCK" ] || fail "could not extract production PlatformType.pkgConfigPath"

SWIFT_PATCH_DIR="$FIXTURE/Sources/BuildScripts/patch/fixture"
mkdir -p "$SWIFT_PATCH_DIR"
cp "$FIXTURE_PATCH" "$SWIFT_PATCH_DIR/0001-approved.patch"
cp "$FIXTURE/base-package.swift" "$SOURCE/Package.swift"
cp "$FIXTURE/base-swift-recipe.patch" "$SOURCE/$FIXTURE_SWIFT_RECIPE"

SWIFT_HELPER="$FIXTURE/spikegit-fixture.swift"
{
  printf '%s\n' \
    'import Foundation' \
    'import Darwin' \
    '' \
    'enum Library: String, CaseIterable {' \
    '    case fixture, FFmpeg, openssl' \
    '}' \
    '' \
    'extension URL {' \
    '    static var currentDirectory: URL {' \
    '        URL(fileURLWithPath: FileManager.default.currentDirectoryPath)' \
    '    }' \
    '' \
    '    static func + (left: URL, right: String) -> URL {' \
    '        left.appendingPathComponent(right)' \
    '    }' \
    '' \
    '    static func + (left: URL, right: [String]) -> URL {' \
    '        right.reduce(left) { $0.appendingPathComponent($1) }' \
    '    }' \
    '}' \
    '' \
    'enum Utility {' \
    '    @discardableResult' \
    '    static func shell(_ command: String, isOutput: Bool = false, currentDirectoryURL: URL? = nil, environment: [String: String] = [:]) -> String? {' \
    '        do {' \
    '            return try launch(executableURL: URL(fileURLWithPath: "/bin/bash"), arguments: ["-c", command], isOutput: isOutput, currentDirectoryURL: currentDirectoryURL, environment: environment)' \
    '        } catch {' \
    '            return nil' \
    '        }' \
    '    }' \
    '' \
    '    @discardableResult' \
    '    static func launch(path: String, arguments: [String], isOutput: Bool = false, isPrint: Bool = true, currentDirectoryURL: URL? = nil, environment: [String: String] = [:]) throws -> String {' \
    '        try launch(executableURL: URL(fileURLWithPath: path), arguments: arguments, isOutput: isOutput, currentDirectoryURL: currentDirectoryURL, environment: environment)' \
    '    }' \
    '' \
    '    @discardableResult' \
    '    static func launch(executableURL: URL, arguments: [String], isOutput: Bool = false, isPrint: Bool = true, currentDirectoryURL: URL? = nil, environment: [String: String] = [:]) throws -> String {' \
    '        let task = Process()' \
    '        task.executableURL = executableURL' \
    '        task.arguments = arguments' \
    '        task.currentDirectoryURL = currentDirectoryURL' \
    '        var mergedEnvironment = ProcessInfo.processInfo.environment' \
    '        environment.forEach { mergedEnvironment[$0.key] = $0.value }' \
    '        task.environment = mergedEnvironment' \
    '        let outputPipe = Pipe()' \
    '        task.standardOutput = outputPipe' \
    '        let errorPipe = Pipe()' \
    '        task.standardError = errorPipe' \
    '        try task.run()' \
    '        task.waitUntilExit()' \
    '        guard task.terminationStatus == 0 else {' \
    '            let diagnostic = String(data: errorPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""' \
    '            if !diagnostic.isEmpty { FileHandle.standardError.write(Data(diagnostic.utf8)) }' \
    '            throw NSError(domain: "SpikeGitFixture", code: Int(task.terminationStatus))' \
    '        }' \
    '        guard isOutput else { return "" }' \
    '        return String(data: outputPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?.trimmingCharacters(in: .newlines) ?? ""' \
    '    }' \
    '}' \
    ''
  sed -n '1,$p' "$SPIKE_BLOCK"
  cat <<'SWIFT_TYPES'

enum ArchType: String { case arm64, x86_64, arm64e }
enum PlatformType: String {
    case xros, xrsimulator, maccatalyst, macos, isimulator, tvsimulator, tvos, ios
SWIFT_TYPES
  cat "$PKG_CONFIG_BLOCK"
  cat <<'SWIFT_BASE'
}

class BaseBuild {
    static let defaultPath = "/usr/bin:/bin:/usr/sbin:/sbin"
    func cFlags(platform: PlatformType, arch: ArchType) -> [String] { ["fixture-cflags"] }
    func ldFlags(platform: PlatformType, arch: ArchType) -> [String] { ["fixture-ldflags"] }
SWIFT_BASE
  cat "$ENVIRONMENT_BLOCK"
  cat <<'SWIFT_ISOLATION'
}

func requireFixture(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    guard condition() else {
        throw NSError(domain: "TargetIsolationFixture", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: message])
    }
}

func writeModule(_ name: String, version: String = "1.0", requires: String = "", in directory: URL) throws {
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let contents = """
    Name: \(name)
    Description: Synthetic target-isolation contract sentinel
    Version: \(version)
    Requires.private: \(requires)
    Libs: -lvortx_sentinel

    """
    try contents.write(to: directory.appendingPathComponent("\(name).pc"), atomically: true, encoding: .utf8)
}

func runTargetIsolation(executable: String, root: URL) throws {
    let manager = FileManager.default
    try manager.createDirectory(at: root, withIntermediateDirectories: true)
    try requireFixture(manager.changeCurrentDirectoryPath(root.path), "could not enter pkg-config fixture")
    let hostPath = root.appendingPathComponent("host-path")
    let hostLibdir = root.appendingPathComponent("host-libdir")
    try requireFixture(ProcessInfo.processInfo.environment["PKG_CONFIG_PATH"] == hostPath.path,
                       "parent PKG_CONFIG_PATH poison was not supplied")
    try requireFixture(ProcessInfo.processInfo.environment["PKG_CONFIG_LIBDIR"] == hostLibdir.path,
                       "parent PKG_CONFIG_LIBDIR poison was not supplied")
    try writeModule("vortx-host-path-sentinel", in: hostPath)
    try writeModule("vortx-host-libdir-sentinel", in: hostLibdir)
    try writeModule("vortx-transitive-host-sentinel", in: hostPath)
    // A duplicate target identity makes host precedence observable by its version.
    try writeModule("vortx-target-sentinel", version: "9.0", in: hostPath)
    let poison = ["PKG_CONFIG_PATH": hostPath.path, "PKG_CONFIG_LIBDIR": hostLibdir.path]
    for module in ["vortx-host-path-sentinel", "vortx-host-libdir-sentinel"] {
        _ = try Utility.launch(path: executable, arguments: ["--exists", module], environment: poison)
    }

    let cases: [(PlatformType, ArchType)] = [
        (.tvos, .arm64), (.tvos, .arm64e), (.tvsimulator, .x86_64),
        (.ios, .arm64), (.macos, .arm64),
    ]
    for (platform, arch) in cases {
        let environment = BaseBuild().environment(platform: platform, arch: arch)
        let getter = platform.pkgConfigPath(arch: arch)
        try requireFixture(environment["PKG_CONFIG_LIBDIR"] == getter, "environment did not use production target getter")
        try requireFixture(environment["PKG_CONFIG_PATH"] == "", "environment must explicitly clear PKG_CONFIG_PATH")
        let paths = getter.components(separatedBy: ":")
        let suffix = "\(platform.rawValue)/thin/\(arch.rawValue)/lib/pkgconfig"
        let expected = ["fixture", "FFmpeg", "openssl"].map { root.appendingPathComponent("\($0)/\(suffix)").path }
        try requireFixture(paths == expected, "target roots differ, contain host roots, or contain empty segments: \(getter)")
        try requireFixture(paths.count == Library.allCases.count && !paths.contains(""), "target root list is not finite and explicit")
        try requireFixture(paths.allSatisfy { !manager.fileExists(atPath: $0) }, "absent dependency roots were not retained")
        let target = URL(fileURLWithPath: paths[1])
        try writeModule("vortx-target-sentinel", in: target)
        try writeModule("vortx-needs-host-sentinel", requires: "vortx-transitive-host-sentinel", in: target)
        // Positive control proves the host dependency exists and can satisfy the
        // target's private dependency if host search is accidentally permitted.
        var contaminated = poison
        contaminated["PKG_CONFIG_PATH"] = "\(hostPath.path):\(target.path)"
        _ = try Utility.launch(path: executable, arguments: ["--static", "--libs", "vortx-needs-host-sentinel"], environment: contaminated)
        let version = try Utility.launch(path: executable, arguments: ["--modversion", "vortx-target-sentinel"], isOutput: true, environment: environment)
        try requireFixture(version == "1.0", "target metadata was missing or shadowed by the host sentinel")
        for module in ["vortx-host-path-sentinel", "vortx-host-libdir-sentinel"] {
            let result = try? Utility.launch(path: executable, arguments: ["--exists", module], environment: environment)
            try requireFixture(result == nil, "host-only module leaked into target search: \(module)")
        }
        let transitive = try? Utility.launch(path: executable, arguments: ["--static", "--libs", "vortx-needs-host-sentinel"], environment: environment)
        try requireFixture(transitive == nil, "missing target private dependency was satisfied by host metadata")
    }

    // No roots exist for this different platform/architecture. The getter must
    // still set nonempty LIBDIR, and the resolver must fail despite host poison.
    let missing = BaseBuild().environment(platform: .xrsimulator, arch: .arm64e)
    let missingPaths = (missing["PKG_CONFIG_LIBDIR"] ?? "").components(separatedBy: ":")
    try requireFixture(missingPaths.count == Library.allCases.count && !missingPaths.contains(""), "absent roots produced empty LIBDIR")
    try requireFixture(missingPaths.allSatisfy { !manager.fileExists(atPath: $0) }, "missing target fixture unexpectedly exists")
    let absent = try? Utility.launch(path: executable, arguments: ["--exists", "vortx-target-sentinel"], environment: missing)
    try requireFixture(absent == nil, "nonexistent target roots fell back to host metadata")
    print("TARGET ISOLATION FIXTURE PASS")
}

do {
    if CommandLine.arguments[1] == "--target-isolation" {
        try runTargetIsolation(executable: CommandLine.arguments[2], root: URL(fileURLWithPath: CommandLine.arguments[3]))
    } else {
        let expectedSHA = CommandLine.arguments[1]
        let checkout = URL(fileURLWithPath: CommandLine.arguments[2])
        try SpikeGit.clonePinned(url: "unused", sha: expectedSHA, library: .fixture, into: checkout)
        print("SPIKE FIXTURE PASS")
    }
    exit(0)
} catch {
    print("PRODUCTION FIXTURE FAILURE: \(error.localizedDescription)")
    exit(1)
}
SWIFT_ISOLATION
} >"$SWIFT_HELPER"

SWIFT_BINARY="$FIXTURE/spikegit-fixture"
swiftc -j 1 -num-threads 1 "$SWIFT_HELPER" -o "$SWIFT_BINARY" || fail "actual patched SpikeGit fixture did not compile"

# The Process scaffold deliberately merges its poisoned parent environment, then
# overlays the actual production dictionary. Production Utility.launch instead
# carries HOME/default PATH only; this fixture does not claim it inherits poison.
PKG_CONFIG_PATH="$FIXTURE/pkg-config/host-path" \
  PKG_CONFIG_LIBDIR="$FIXTURE/pkg-config/host-libdir" \
  "$SWIFT_BINARY" --target-isolation "$PKG_CONFIG_EXECUTABLE" "$FIXTURE/pkg-config" \
  >"$FIXTURE/target-isolation.log" 2>&1 ||
  fail "compiled production target-isolation fixture failed: $(cat "$FIXTURE/target-isolation.log")"

run_spike() {
  local expected_sha="$1" log="$2"
  (cd "$SOURCE" && "$SWIFT_BINARY" "$expected_sha" "$SOURCE") >"$log" 2>&1
}

run_spike "$FIXTURE_REF" "$FIXTURE/swift-clean.log" ||
  fail "compiled SpikeGit rejected the initial clean checkout: $(cat "$FIXTURE/swift-clean.log")"
git -C "$SOURCE" apply "$FIXTURE_PATCH"
run_spike "$FIXTURE_REF" "$FIXTURE/swift-retry.log" ||
  fail "compiled SpikeGit rejected the exact patched retry: $(cat "$FIXTURE/swift-retry.log")"

BAD_REF="0000000000000000000000000000000000000000"
if run_spike "$BAD_REF" "$FIXTURE/swift-wrong-sha.log"; then
  fail "compiled SpikeGit accepted a wrong SHA"
fi
grep -Fq 'expected' "$FIXTURE/swift-wrong-sha.log" || fail "wrong SHA lacked the exact-pin diagnostic"

printf '%s\n' 'let unrelated = true' >>"$SOURCE/Package.swift"
if run_spike "$FIXTURE_REF" "$FIXTURE/swift-unrelated.log"; then
  fail "compiled SpikeGit accepted an unrelated same-file edit"
fi
grep -Fq 'conflicting, partial, or unrelated' "$FIXTURE/swift-unrelated.log" ||
  fail "compiled SpikeGit same-file rejection lacked the exact-tree diagnostic"
cp "$FIXTURE/exact-package.swift" "$SOURCE/Package.swift"

printf '%s\n' 'unrelated Swift recipe edit' >>"$SOURCE/$FIXTURE_SWIFT_RECIPE"
if run_spike "$FIXTURE_REF" "$FIXTURE/swift-unrelated-recipe.log"; then
  fail "compiled SpikeGit accepted an unrelated approved-recipe edit"
fi
grep -Fq 'conflicting, partial, or unrelated' "$FIXTURE/swift-unrelated-recipe.log" ||
  fail "compiled SpikeGit recipe rejection lacked the exact-tree diagnostic"
cp "$FIXTURE/exact-swift-recipe.patch" "$SOURCE/$FIXTURE_SWIFT_RECIPE"

printf '%s\n' 'untracked Swift fixture data' >"$SOURCE/swift-untracked.txt"
if run_spike "$FIXTURE_REF" "$FIXTURE/swift-untracked.log"; then
  fail "compiled SpikeGit accepted an unknown untracked file"
fi
grep -Fq 'not owned by the approved patch set' "$FIXTURE/swift-untracked.log" ||
  fail "compiled SpikeGit untracked rejection lacked the ownership diagnostic"

echo "PASS: compiled production SpikeGit and target pkg-config isolation, MPVKit-DVFEL pin/dirty-state, FFmpeg/Libmpv capability, exact slices, TLS, and post-fetch artifact contracts"
