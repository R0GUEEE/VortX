import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { method } from './kotlin-source-boundary.mjs';
const packet=dirname(fileURLToPath(import.meta.url));
const repo=process.argv[2] ?? fileURLToPath(new URL('../../../',import.meta.url));
const source=join(repo,'android/app/src/main/kotlin/com/vortx/android');
const read=path=>readFileSync(join(source,path),'utf8');
const fixture=path=>readFileSync(join(packet,path),'utf8');
const hash=text=>createHash('sha256').update(text).digest('hex');
const manifest=JSON.parse(fixture('tests-source-manifest.json'));
const lexicalMarker='    fun lexical(';
const lexicalBoundary=[
  '    fun lexical(value: String = "{") = outer {',
  '        val ordinary = "{ ignored }"',
  '        val raw = """ { raw } """',
  "        val character = '}'",
  '        val template = "${nested("}")}"',
  '        // } ignored',
  '        /* { nested /* } */ } */',
  '        inner { true }',
  '    }',
].join('\n')+'\n';
assert.equal(method(lexicalBoundary+'    fun following() { false }\n',lexicalMarker),lexicalBoundary);
assert.throws(()=>method(lexicalBoundary+lexicalBoundary,lexicalMarker),/Missing\/ambiguous/);
assert.throws(()=>method('    fun broken() { /* never closed', '    fun broken('),/Unterminated/);
const access=read('engine/NativeProfileAccess.kt'), store=read('profile/ProfileStore.kt');
const session=read('engine/VortxNativeSession.kt'), coordinator=read('engine/NativeAccountCoordinator.kt');
const accessExtract=fixture('tests-extracted/NativeProfileAccess.kt');
const storeExtract=fixture('tests-extracted/ProfileStoreBoundary.kt');
const bootstrapExtract=fixture('tests-extracted/ProfileBootstrapBoundary.kt');
const sessionExtract=fixture('tests-session-fixture.kt');
const constructor=access.slice(access.indexOf('internal class NativeProfileAccess('),access.indexOf(') : NativeProfileGateway {')+') : NativeProfileGateway {'.length)+'\n';
const readMethod='    override fun read(): NativeProfileGateway.Projection = projection(session().read())\n';
assert(access.includes(readMethod) && accessExtract.includes(constructor) && accessExtract.includes(readMethod));
const snippets={constructor,read:readMethod};
for(const [name,marker] of [['withProjection','    override fun <T> withProjection('],['currentProjection','    override fun <T> withCurrentProjection('],['prepareProjection','    override fun prepareProjection(']]) {
  snippets[name]=method(access,marker); assert.equal(method(accessExtract,marker),snippets[name]);
}
assert(!snippets.withProjection.includes('override fun <T> withCurrentProjection('), 'An expression body must not swallow the following method');
snippets.storeBoundary=method(store,'    private fun applyNativeProjection(');
assert.equal(method(storeExtract,'    private fun applyNativeProjection('),snippets.storeBoundary);
for(const [name,marker] of [['owned','    @Synchronized fun <T> owned('],['currentRead','    @Synchronized internal fun <T> withCurrentRead(']]) {
  snippets[name]=method(session,marker);assert.equal(method(sessionExtract,marker),snippets[name]);
}
for(const [name,path] of [['gateway','NativeProfileGateway.kt'],['cwGate','ContinueWatchingOwnerGate.kt']]) {
  snippets[name]=read('profile/'+path);assert.equal(fixture('tests-extracted/'+path),snippets[name]);
}
for(const [name,text] of Object.entries(snippets)) assert.equal(hash(text),manifest.hashes[name],`${name}: reviewed hash changed`);
for(const marker of ['    internal suspend fun attachNativeGateway(','    internal suspend fun refreshNativeProjectionInBackground(','    private fun prepareNativeProjection(','    private fun applyPreparedNativeProjection(','    internal fun clearNativeProjection(']) assert.equal(method(bootstrapExtract,marker),method(store,marker));
const fields=store.slice(store.indexOf('    private var nativeProjectionRequest ='),store.indexOf('    /** Bootstrap remains suspended'));
assert(bootstrapExtract.includes(fields),'Bootstrap fields/basis/receipt types must match actual source');
const capture=method(coordinator,'    internal fun captureProfilePresentation(');
assert.equal(method(fixture('tests-extracted/ProfilePresentationBoundary.kt'),'    internal fun captureProfilePresentation('),capture);
const discovery=read('profile/ProfileDiscoveryPreferences.kt');
const policy=discovery.slice(discovery.indexOf('data class ProfileDiscoveryPreferences('),discovery.indexOf('/** Bridges a profile'));
assert.equal(fixture('tests-extracted/ContinueWatchingMigration.kt'),'package com.vortx.android.profile\n'+policy.trimEnd()+'\n');
console.log('PASS exact_actual_bootstrap_access_metadata_admission_checkpoint_and_synchronous_publication_sources');

// Existing command/PIN/edit boundary remains live and independent of metadata-only publication.
assert.match(snippets.storeBoundary,/ContinueWatchingOwnerGate\.serialized[\s\S]*withProjection\(value\)/);
assert.match(snippets.currentProjection,/runtime\.withCurrentRead[\s\S]*check\(admit\(runtime\)/);
assert.match(snippets.withProjection,/check\(current == expected\)/);
const add=method(store,'    fun add(profile: UserProfile)');
function nativeAdd(text) {
  assert.match(text,/fun add\(profile: UserProfile\) = ContinueWatchingOwnerGate\.serialized \{/,'Native add must hold CW gate');
  assert.match(text,/applyNativeProjection\(checkNotNull\(nativeGateway\)\.save\(profile, adding = true\)\)/);
}
nativeAdd(add);assert.throws(()=>nativeAdd(add.replace('= ContinueWatchingOwnerGate.serialized {','{')),/Native add must hold CW gate/);
const pending=method(store,'    private fun capturePendingNativeContinueWatchingDiscovery(');
assert.match(pending,/val current = gateway\.read\(\)[\s\S]*gateway\.withProjection\(current\)/);
assert.match(pending,/readbackIsCurrent = \{ saved -> nativeGateway\?\.read\(\) == saved \}/);
assert.match(access,/Open this profile through its PIN gate before editing/);
assert.match(access,/Profile editor source changed/);

// Final Main effects have no Session/provider/secure access, including switch-hook callers.
const apply=method(store,'    private fun applyPreparedNativeProjection(');
assert.doesNotMatch(apply,/\.read\(|NativeProviderAccess\.(?:capture|current|guarded)|currentSessionEpoch|\.save\(/);
assert.match(apply,/withoutNativePreferenceCapture/);
const attach=method(store,'    internal suspend fun attachNativeGateway(');
assert.match(attach,/withContext\(Dispatchers\.IO\)[\s\S]*gateway\.prepareProjection/);
assert.match(attach,/prepared\.publishIfCurrent/);
assert.match(attach,/NativeProviderAccess\.revision\.value == currentEffects\.providerRevision/);
assert.match(attach,/if \(prepared\.requiresRecovery\)[\s\S]*continueWatchingMigration\.restore[\s\S]*check\(!recoveryRequired\)/);
assert.match(attach,/check\(\+\+attempt < 4\)[\s\S]*delay\(50L \* attempt\)/);
const prepare=method(store,'    private fun prepareNativeProjection(');
assert.match(prepare,/NativeProviderAccess\.capture/);assert.match(prepare,/TraktAuth\.currentSessionEpoch/);
assert.match(prepare,/Native provider authority is unavailable/);
assert.match(prepare,/previousAcknowledgement\?\.takeIf/);
const publicationTail=capture.slice(capture.indexOf('PreparedProjection('));
assert.doesNotMatch(publicationTail,/session\.(?:read|owned|withCurrentRead)|captureProfilePresentationAdmission\(/);
assert.match(publicationTail,/mounted\.get\(\) === current && changes\.value == epoch && session\.updates\.value == version/);
const forYou=read('foryou/ForYouStore.kt');
const forYouExtract=fixture('tests-extracted/ForYouReloadBoundary.kt');
const forYouBody=forYou.slice(forYou.indexOf('class ForYouStore internal constructor('),forYou.indexOf('    suspend fun save(interests:')).replace('class ForYouStore internal constructor(','class ExtractedForYouStore internal constructor(');
assert(forYouExtract.includes(forYouBody),'Entire actual ForYou reload class body must match');
assert(forYouExtract.includes(forYou.slice(forYou.indexOf('interface ForYouNativeGateway {'),forYou.indexOf('/**\n * The active profile'))),'Actual ForYou Snapshot and State must match');
assert.match(forYou,/registerProfileSwitch \{ reload\(invalidate = true\) \}/);
assert.match(forYou,/private suspend fun reload\([\s\S]*withContext\(ioDispatcher\)[\s\S]*reloadMutex\.withLock[\s\S]*currentCoroutineContext\(\)\.ensureActive\(\)/);
assert.match(forYou,/generation == reloadGeneration && gateway === native/);
assert.match(forYou,/captured\?\.publishIfCurrent\(publication\)/);
const watchlist=read('library/WatchlistStore.kt');
assert.match(method(watchlist,'    private fun scheduleReload('),/scope\.launch\(ioDispatcher\) \{ reloadAttempt\(attempt\) \}/);
const app=read('VortXApplication.kt');
assert.match(app,/candidate -> ProfileStore\.sharedOrNull\(\)\?\.attachNativeGateway\(nativeProfiles, candidate\)/);
assert.doesNotMatch(app,/nativeAccounts\.session\(\)\.also \{ it\.read\(\) \}/);
const readOnly=app.slice(app.indexOf('captureProfilePresentationAdmission ='),app.indexOf('internal fun nativeStreamingAccounts'));
assert.match(readOnly,/manager\.sessionOwnerSnapshot\(\) != captured/);
const metadataGate=readOnly.slice(readOnly.indexOf('val gate:'));
assert.doesNotMatch(metadataGate,/sessionOwnerSnapshot\(/);
assert.match(metadataGate,/manager\.sessionUiState\.value is VortXSyncManager\.SessionUiState\.SignedIn/);
// Generic mutation callback still performs its original persisted-identity revalidation.
assert.match(app,/syncManager === manager && manager\.sessionOwnerSnapshot\(\) == captured && action\(\)/);
assert.match(read('sync/VortXSyncManager.kt'),/profileStore\.refreshNativeProjectionInBackground\(forceSettings = true\)/);
assert.match(read('sync/VortXSyncManager.kt'),/val isSignedIn: Boolean get\(\) = session != null/);
console.log('PASS metadata_only_main_effects_preserve_live_commands_and_background_dependent_capture_contracts');
console.log('INFO source and scheduling collaborators only; no full Android/JNI/device crash proof');
