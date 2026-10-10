#!/usr/bin/env node
import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, mkdirSync, mkdtempSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {resolve, join} from 'node:path';
const root = resolve(import.meta.dirname, '..');
process.chdir(root);
const manager = readFileSync('app/SourcesShared/VortXSyncManager.swift', 'utf8');
const start = manager.indexOf('    private func pushSyncDocAt(');
const end = manager.indexOf('\n    }', start);
assert(start >= 0 && end > start);
const verdict = manager.split('\n').find(line => line.includes('private enum PushOutcome'));
assert(verdict);
const template = readFileSync('app/Tests/SyncBackupUploadTransportTests.swift', 'utf8');
mkdirSync('app/build', {recursive: true});
const directory = mkdtempSync('app/build/sync-backup-upload.');
const source = join(directory, 'ProductionUpload.swift');
writeFileSync(source, template.replace('    /* PRODUCTION_UPLOAD */', verdict + '\n' + manager.slice(start, end + 6)));
execFileSync('xcrun', ['swiftc', '-parse-as-library', '-swift-version', '6', '-strict-concurrency=complete',
  '-warnings-as-errors', '-D', 'VORTX_NATIVE_DATA_ENGINE', 'app/SourcesShared/SyncBackupUploadFailure.swift',
  source, '-o', join(directory, 'checks')], {stdio: 'inherit'});
execFileSync(join(directory, 'checks'), [], {stdio: 'inherit'});
console.log(`Retained actual upload-method fixture: ${resolve(directory)}`);
