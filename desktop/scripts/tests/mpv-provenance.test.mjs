import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync, cpSync, chmodSync, symlinkSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const app = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const updates = new Map(['desktop/scripts/fetch-server-deps.sh', 'desktop/src-tauri/src/player.rs', 'desktop/scripts/tests/fetch-server-deps-integrity.test.sh'].map(path => [path, readFileSync(join(app, path), 'utf8')]));
const fixtureRoot = join(app, '_build');
mkdirSync(fixtureRoot, { recursive: true });
function put(path, text, mode) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, text, { mode }); }
function run(cmd, args, cwd, extra = {}) { return spawnSync(cmd, args, { cwd, env: { ...process.env, ...extra }, encoding: 'utf8', maxBuffer: 1024 * 1024 }); }
const scratch = mkdtempSync(join(fixtureRoot, 'desktop-provenance-test.'));
try {
  const fixed = updates.get('desktop/scripts/fetch-server-deps.sh');
  const source = fixed;
  put(join(scratch, 'fetch.sh'), fixed);
  assert.equal(run('/bin/bash', ['-n', 'fetch.sh'], scratch).status, 0);
  const cleanupStart = source.indexOf('TEMP_PATHS=()'), cleanupEnd = source.indexOf('normalize_sha256()');
  const oldCleanup = run('/bin/bash', ['-uc', source.slice(cleanupStart, cleanupEnd).replace('${TEMP_PATHS[@]-}', '${TEMP_PATHS[@]}')], scratch);
  const newCleanup = run('/bin/bash', ['-uc', fixed.slice(fixed.indexOf('TEMP_PATHS=()'), fixed.indexOf('normalize_sha256()'))], scratch);
  assert.match(oldCleanup.stderr, /unbound variable/);
  assert.equal(newCleanup.status, 0, newCleanup.stderr);
  console.log('PASS actual empty-cache EXIT trap: Bash 3.2 original fails nounset, repair handles empty array');
  const contracts = join(scratch, 'contracts');
  for (const path of ['.github/workflows/desktop.yml', '.github/workflows/security.yml']) put(join(contracts, path), readFileSync(join(app, path)));
  for (const [path, text] of updates) put(join(contracts, path), text, path.endsWith('.sh') ? 0o755 : 0o644);
  const contractRun = run('/bin/bash', [join(contracts, 'desktop/scripts/tests/fetch-server-deps-integrity.test.sh')], contracts, { TMPDIR: scratch });
  assert.equal(contractRun.status, 0, contractRun.stdout + contractRun.stderr);
  console.log('PASS existing dependency integrity source contracts and their mutation controls');
  // Extract the actual Windows/macOS admission branches and their unchanged digest verifier.
  // Only fixture archive digest constants change; no package download or player is executed.
  function extract(full) {
    const digest = full.slice(full.indexOf('verify_sha256()'), full.indexOf('# Pinned Node LTS.'));
    const helperStart = full.indexOf('fetch_verified_mpv_archive()');
    const helpers = helperStart < 0 ? '' : full.slice(helperStart, full.indexOf('# --- 1) Node runtime'));
    // Stable endpoint markers, rather than a model of production branch behavior.
    const mpvStart = full.indexOf('case "${uname_s}" in', full.indexOf('# dynamically linked'));
    assert(mpvStart > 0);
    const mpv = full.slice(mpvStart, full.indexOf('# The Tauri build bundles', mpvStart));
    return `#!/bin/bash\nset -euo pipefail\nfail(){ echo "$*" >&2; exit 1; }\nTEMP_PATHS=()\ncleanup(){ for p in "\${TEMP_PATHS[@]-}"; do [ -z "$p" ] || rm -rf -- "$p"; done; }\ntrap cleanup EXIT\n${digest}\n${helpers}\n${mpv}`;
  }
  const repairedBranch = extract(fixed);
  const bin = join(scratch, 'bin');
  put(join(bin, 'curl'), '#!/bin/bash\nprintf "called\\n" >> "$FIXTURE_CURL_LOG"\ncp "$FIXTURE_ARCHIVE" "${@: -1}"\n', 0o755);
  put(join(bin, '7z'), '#!/bin/bash\ncp "$FIXTURE_EXE" mpv.exe\n', 0o755);
  const bundle = join(scratch, 'archive-input/mpv.app');
  put(join(bundle, 'Contents/MacOS/mpv'), 'fixture executable'); chmodSync(join(bundle, 'Contents/MacOS/mpv'), 0o755);
  put(join(bundle, 'Contents/Frameworks/libfixture.dylib'), 'fixture dylib');
  symlinkSync('libfixture.dylib', join(bundle, 'Contents/Frameworks/libfixture-current.dylib'));
  const macArchive = join(scratch, 'mac.tar.gz');
  assert.equal(run('/usr/bin/tar', ['-czf', macArchive, '-C', dirname(bundle), 'mpv.app'], scratch).status, 0);
  const winArchive = join(scratch, 'win.7z'), exe = join(scratch, 'mpv.exe');
  put(winArchive, 'fixture archive bytes'); put(exe, 'fixture exe bytes');
  const macDigest = createHash('sha256').update(readFileSync(macArchive)).digest('hex');
  const winDigest = createHash('sha256').update(readFileSync(winArchive)).digest('hex');
  function branchRun(script, platform, mutate = () => {}, cache = true) {
    const fixture = mkdtempSync(join(scratch, `${platform}.`));
    const res = join(fixture, 'resources'), cacheDir = join(res, '.verified-downloads');
    mkdirSync(cacheDir, { recursive: true });
    const destination = join(res, platform === 'mac' ? 'mpv-darwin-arm64.app' : 'mpv-win-x64.exe');
    cpSync(platform === 'mac' ? bundle : exe, destination, { recursive: true, verbatimSymlinks: true });
    const archive = platform === 'mac' ? macArchive : winArchive;
    const cacheName = platform === 'mac' ? 'mpv-arm64-0.40.0.tar.gz' : 'mpv-x86_64-20260720-git-94335ab87a.7z';
    if (cache) cpSync(archive, join(cacheDir, cacheName));
    const curlLog = join(fixture, 'curl.log'); put(curlLog, '');
    mutate({ destination, cached: join(cacheDir, cacheName) });
    put(join(fixture, 'branch.sh'), script.replaceAll('3170fb709defebaba33e9755297d70dc3562220541de54fc3d494a8309ef1260', macDigest).replaceAll('a77cd53dc8d9e002b612d3bfaffa15be3ca2944713caab508b6f4f26a75400b3', winDigest));
    const result = run('/bin/bash', ['branch.sh'], fixture, { PATH: `${bin}:${process.env.PATH}`, RES_DIR: res, NODE_DEST: process.execPath,
      uname_s: platform === 'mac' ? 'Darwin' : 'MINGW64_NT', uname_m: 'arm64', TMPDIR: fixture,
      FIXTURE_CURL_LOG: curlLog, FIXTURE_ARCHIVE: archive, FIXTURE_EXE: exe });
    return { ...result, curls: readFileSync(curlLog, 'utf8') };
  }
  for (const platform of ['mac', 'win']) {
    const corrupt = ({ destination }) => put(platform === 'mac' ? join(destination, 'Contents/MacOS/mpv') : destination, 'modified executable');
    const denied = branchRun(repairedBranch, platform, corrupt); assert.notEqual(denied.status, 0, denied.stdout + denied.stderr); assert.equal(denied.curls, '');
    const valid = branchRun(repairedBranch, platform); assert.equal(valid.status, 0, valid.stdout + valid.stderr); assert.equal(valid.curls, '');
    const invalidArchive = branchRun(repairedBranch, platform, ({ cached }) => put(cached, 'corrupt archive')); assert.notEqual(invalidArchive.status, 0); assert.match(invalidArchive.stderr, /checksum mismatch/);
    const hydrated = branchRun(repairedBranch, platform, () => {}, false); assert.equal(hydrated.status, 0, hydrated.stderr); assert.equal(hydrated.curls, 'called\n');
  }
  for (const mutate of [
    ({ destination }) => put(join(destination, 'Contents/Frameworks/libfixture.dylib'), 'wrong dylib'),
    ({ destination }) => put(join(destination, 'injected-file'), 'unexpected'),
    ({ destination }) => { const link = join(destination, 'Contents/Frameworks/libfixture-current.dylib'); rmSync(link); symlinkSync('wrong-target.dylib', link); },
    ({ destination }) => chmodSync(join(destination, 'Contents/MacOS/mpv'), 0o644),
  ]) assert.notEqual(branchRun(repairedBranch, 'mac', mutate).status, 0);
  console.log('PASS actual macOS/Windows branch: source verifies cached archive and complete payload, rejects executable/dylib/extra-file/symlink/mode corruption, accepts verified reuse/fixture download');

  function resolveUnit(full) {
    const start = full.indexOf('fn resolve_mpv_binary('), end = full.indexOf('/// A fresh, process-unique IPC endpoint', start);
    return `use std::path::{Path, PathBuf};\nfn mpv_binary_name() -> &'static str { "packaged-mpv" }\n${full.slice(start, end)}\nfn main() { let path = std::env::args_os().nth(1).unwrap(); println!("{}", resolve_mpv_binary(Path::new(&path)).map(|p|p.display().to_string()).unwrap_or_else(||"NONE".to_owned())); }\n`;
  }
  const newPlayer = updates.get('desktop/src-tauri/src/player.rs');
  const resource = join(scratch, 'resolve-resources'); mkdirSync(resource);
  put(join(bin, 'mpv'), 'fixture never executed', 0o755);
  for (const [name, full, debug] of [['after-release', newPlayer, false], ['after-debug', newPlayer, true]]) {
    const unit = join(scratch, `${name}.rs`), binary = join(scratch, name); put(unit, resolveUnit(full));
    const compiled = run('rustc', ['--edition=2021', '-C', `debug-assertions=${debug ? 'yes' : 'no'}`, unit, '-o', binary], scratch); assert.equal(compiled.status, 0, compiled.stderr);
    const result = run(binary, [resource], scratch, { PATH: bin }); assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), name === 'after-release' ? 'NONE' : join(bin, 'mpv'));
    put(join(resource, 'packaged-mpv'), 'fixture packaged runtime');
    assert.equal(run(binary, [resource], scratch, { PATH: bin }).stdout.trim(), join(resource, 'packaged-mpv'));
    rmSync(join(resource, 'packaged-mpv'));
    mkdirSync(join(resource, 'packaged-mpv'));
    const directoryResult = run(binary, [resource], scratch, { PATH: bin });
    assert.equal(directoryResult.stdout.trim(), name === 'before-release' ? join(resource, 'packaged-mpv') : name === 'after-release' ? 'NONE' : join(bin, 'mpv'));
    rmSync(join(resource, 'packaged-mpv'), { recursive: true });
  }
  console.log('PASS actual resolver compiled debug/release: release fails closed, debug retains fallback, bundled file wins and directory is rejected');
} finally { rmSync(scratch, { recursive: true, force: true }); }
