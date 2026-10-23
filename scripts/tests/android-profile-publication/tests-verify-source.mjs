import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const packet = dirname(fileURLToPath(import.meta.url));
const repo = process.argv[2] ?? fileURLToPath(new URL('../../../', import.meta.url));
const root = join(repo, 'android/app/src/main/kotlin/com/vortx/android');
const read = path => readFileSync(join(root, path), 'utf8');
const manifest = JSON.parse(readFileSync(join(packet, 'tests-source-manifest.json'), 'utf8'));
const hash = text => createHash('sha256').update(text).digest('hex');
function method(text, marker) {
  const start = text.indexOf(marker);
  assert(start >= 0 && text.indexOf(marker, start + marker.length) < 0, `Missing/ambiguous method ${marker}`);
  const end = text.indexOf('\n    }', start);
  assert(end > start, `Missing method end ${marker}`);
  return text.slice(start, end + '\n    }'.length) + '\n';
}
const access = read('engine/NativeProfileAccess.kt');
const store = read('profile/ProfileStore.kt');
const session = read('engine/VortxNativeSession.kt');
const accessExtract = readFileSync(join(packet, 'tests-extracted/NativeProfileAccess.kt'), 'utf8');
const storeExtract = readFileSync(join(packet, 'tests-extracted/ProfileStoreBoundary.kt'), 'utf8');
const sessionFixture = readFileSync(join(packet, 'tests-session-fixture.kt'), 'utf8');
const accessStart = access.indexOf('internal class NativeProfileAccess(');
const accessEnd = access.indexOf(') : NativeProfileGateway {', accessStart) + ') : NativeProfileGateway {'.length;
const constructor = access.slice(accessStart, accessEnd) + '\n';
const readMethod = access.match(/^    override fun read\(\): NativeProfileGateway\.Projection = projection\(session\(\)\.read\(\)\)\n/m)?.[0];
assert(accessExtract.includes(constructor) && accessExtract.includes(readMethod), 'Constructor/read must be exact source');
const snippets = {
  constructor,
  read: readMethod,
  withProjection: method(access, '    override fun <T> withProjection('),
  storeBoundary: method(store, '    private fun applyNativeProjection('),
  owned: method(session, '    @Synchronized fun <T> owned('),
};
assert.equal(method(accessExtract, '    override fun <T> withProjection('), snippets.withProjection);
assert.equal(method(storeExtract, '    private fun applyNativeProjection('), snippets.storeBoundary);
assert.equal(method(sessionFixture, '    @Synchronized fun <T> owned('), snippets.owned);
for (const [name, content] of Object.entries(snippets)) assert.equal(hash(content), manifest.hashes[name], `${name} changed; refresh/review extraction`);
for (const [name, path] of [['gateway', 'NativeProfileGateway.kt'], ['cwGate', 'ContinueWatchingOwnerGate.kt']]) {
  const actual = read(`profile/${path}`);
  assert.equal(readFileSync(join(packet, 'tests-extracted', path), 'utf8'), actual, `${name}: full source copy changed`);
  assert.equal(hash(actual), manifest.hashes[name]);
}
assert.match(session, /@Synchronized fun read\(\): VortxNativeRead/);
console.log('PASS exact_actual_access_constructor_read_publication_store_boundary_session_owned_and_cw_gate_sources');

assert.match(snippets.storeBoundary, /= ContinueWatchingOwnerGate\.serialized \{\s*checkNotNull\(nativeGateway\)\.withProjection\(value\) \{ applyNativeProjectionLocked\(value, forceSettings\) \}/);
assert.equal((store.match(/applyNativeProjectionLocked\(/g) ?? []).length, 2, 'Only the CW/Session boundary may call the locked reducer');
const add = method(store, '    fun add(profile: UserProfile)');
assert.match(add, /applyNativeProjection\(checkNotNull\(nativeGateway\)\.save\(profile, adding = true\)\)/);
assert(!add.includes('applyNativeProjectionLocked('));
const pending = method(store, '    private fun capturePendingNativeContinueWatchingDiscovery(');
assert.match(pending, /return gateway\.withProjection\(gateway\.read\(\)\) \{\s*val captured = com\.vortx\.android\.integrations\.NativeProviderAccess\.capture\(/);
assert.match(pending, /applyNativeProjection\(saved, forceSettings = true\)/);
assert.match(snippets.withProjection, /return runtime\.owned\(owner\) \{[\s\S]*check\(admit\(runtime\) \{[\s\S]*session\(\) === runtime && projection\(runtime\.read\(\)\) == expected[\s\S]*result = runCatching\(action\)/);
console.log('PASS_store_cw_before_session_pending_cw_capture_and_native_add_have_no_reducer_bypass');

const app = read('VortXApplication.kt');
const vm = read('ui/viewmodel/NativeStreamingAccountViewModel.kt');
assert.match(app, /publicationAdmission = \{ runtime, action ->\s*val account = nativeAccounts\.accountFor\(runtime\)\s*account != null && nativeAccounts\.withProfileMutation\(runtime, account, action\)/);
assert.match(vm, /publicationAdmission = \{ runtime, action ->\s*val account = accounts\.accountFor\(runtime\)\s*account != null && accounts\.withProfileMutation\(runtime, account, action\)/);
const coordinator = read('engine/NativeAccountCoordinator.kt');
assert.match(coordinator, /fun withProfileMutation\(session: VortxNativeSession, account: SessionOwnerSnapshot\.Account, action: \(\) -> Boolean\): Boolean =\s*withAccountAdmission\(account, \{ true \}\) \{ withMountedSession\(session, account, action\) \}/);
console.log('PASS_both_production_accessors_keep_existing_account_epoch_and_mounted_lifecycle_admission');
console.log(`INFO actual_access_sha256=${hash(access)} actual_store_sha256=${hash(store)}`);
