#!/usr/bin/env node
// Exact declaration extraction only; no SDK, Session, app container, network or checkpoint lane.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { resolve, join } from 'node:path';
const require = createRequire(import.meta.url);
const { extractDeclaration } = require('./xperience-harness-inputs.cjs');
assert.equal(process.argv.length, 4);
assert.equal(process.argv[2], '--extract');
const output = resolve(process.argv[3]);
const root = resolve(new URL('..', import.meta.url).pathname);
const hash = value => createHash('sha256').update(value).digest('hex');
const paths = {
  facade: 'app/SourcesShared/VortxNativeCoreFacade.swift',
  fifo: 'app/SourcesShared/VortxNativeFIFOAdmission.swift',
  runtime: 'app/SourcesShared/VortxNativeRuntime.swift',
  json: 'app/SourcesShared/VortxResourceBridge.swift',
  authority: 'app/SourcesShared/VortxNativeHostFieldMutation.swift',
  fixture: 'app/Tests/VortxNativeFacadeLockTests.swift',
};
const source = Object.fromEntries(await Promise.all(Object.entries(paths)
  .map(async ([key, path]) => [key, await readFile(join(root, path), 'utf8')])));
const slices = [];
function declaration(key, marker) {
  const text = extractDeclaration(source[key], marker);
  slices.push({ path: paths[key], marker, sourceSHA256: hash(source[key]), declarationSHA256: hash(text) });
  return text;
}
const lock = declaration('facade', 'final class VortxNativeFacadeLock:');
assert(!lock.includes('@unchecked'), 'lock must retain its checked Sendable declaration');
assert.match(source.facade, /private let lock: VortxNativeFacadeLock\b/);
const initializer = declaration('facade', 'private init(session:');
assert.match(initializer, /let lock = VortxNativeFacadeLock\(\)/);
assert.match(initializer, /self\.lock = lock; self\.mutationFIFO = VortxNativeFIFOAdmission\(lock: lock\)/);
assert.match(source.fifo, /private let lock: any NSLocking & Sendable\b/);
assert.match(source.fifo, /init\(lock: any NSLocking & Sendable\)/);
const stage = declaration('facade', 'private func stageChanged(');
assert.match(stage, /let callback = changed/);
assert.match(stage, /lock\.afterOutermostUnlock \{ callback\(fields\) \}/);
const publish = declaration('facade', 'private func publish(');
const registry = declaration('facade', 'func rebindRegistry(');
const mutation = declaration('facade', 'private func enqueueMutation(');
for (const [name, body] of Object.entries({ resourcePublish: publish, registryRebind: registry, acknowledgedMutation: mutation })) {
  assert.equal((body.match(/\bstageChanged\(/g) ?? []).length, 1, name + ' stages exactly one delivery site');
  assert(!/\b(?:self\.)?changed\s*\(/.test(body), name + ' must not call changed directly');
}
const resourceDelivery = publish.indexOf('stageChanged(changedFields.sorted())');
assert(resourceDelivery >= 0 && resourceDelivery < publish.lastIndexOf('lock.unlock()'),
  'resource change is staged before its local unlock');
const accepted = registry.indexOf('let accepted = lock.withLock');
const registryDelivery = registry.indexOf('stageChanged(["ctx"])', accepted);
assert(accepted >= 0 && registryDelivery > accepted && registryDelivery < registry.indexOf('guard accepted else', accepted),
  'registry change is staged within its accepted transaction');
const ack = mutation.indexOf('let publication = try self.lock.withLock');
const release = mutation.indexOf('if profileTransition { self.lock.withLock', ack);
const delivery = mutation.indexOf('self.stageChanged(publication.changed)', release);
const publicationGuard = mutation.indexOf('guard publication != nil', delivery);
assert(ack >= 0 && release > ack && delivery > release && publicationGuard > delivery,
  'ACK projection, transition release, staged changed delivery and completion ordering retained');
assert(mutation.indexOf('self.mutationAccepted()', publicationGuard) > publicationGuard);
assert(mutation.indexOf('completion?(.success(document))', publicationGuard) > publicationGuard);
const declarations = [
  declaration('runtime', 'enum VortxNativeError:'),
  declaration('authority', 'protocol VortxMutationAuthority:'),
  declaration('json', 'indirect enum VortxJSON:'),
  declaration('facade', 'struct VortxLibraryMembership:'),
  lock,
].join('\n\n');
const workerMethods = [
  stage, publish,
  declaration('facade', 'private func string('),
  declaration('facade', 'private func metaWithPlayback('),
  declaration('facade', 'private func externalMetadataFingerprint('),
  declaration('facade', 'private func observeExternalMetadata('),
].join('\n\n');
function replaceOnce(text, marker, value) {
  assert.equal(text.split(marker).length, 2, 'unique fixture marker ' + marker);
  return text.replace(marker, value);
}
let combined = replaceOnce(source.fixture, '// PRODUCTION_DECLARATIONS', declarations);
combined = replaceOnce(combined, '// PRODUCTION_WORKER_METHODS', workerMethods);
await writeFile(join(output, 'Combined.swift'), combined, { flag: 'wx' });
await writeFile(join(output, 'FIFO.swift'), source.fifo, { flag: 'wx' });
await writeFile(join(output, 'source-binding.json'), JSON.stringify({
  format: 'vortx-facade-lock-worker-v1',
  sourceHEAD: process.env.VORTX_FACADE_LOCK_SOURCE_HEAD ?? 'unprovided',
  wholeSourceHashes: Object.fromEntries(Object.entries(paths).map(([key, path]) => [path, hash(source[key])])),
  slices,
  generated: { combinedSHA256: hash(combined), fifoSHA256: hash(source.fifo) },
  sourceOnlyProducerChecks: ['registryRebind', 'acknowledgedMutation'],
  runtimeProducer: 'exact private publish and stageChanged in explicit RAM worker',
  boundary: [
    'Whole production FIFO and lock declarations; existing 14 FIFO XCTest cases unchanged',
    'Nine new synchronous lock/publication worker tests with bounded semaphore worker barriers',
    'No actual app facade, Session task chain, ABI, credential, source-lock release, owner receipt, durability or consumer freshness claim',
    'Insertion order applies to one outer-lock queued batch; unlocked batches can interleave or reenter',
  ],
}, null, 2) + '\n', { flag: 'wx' });
