'use strict';
const assert = require('node:assert/strict');

const observationInputList = 'scripts/xperience-observation-swift-inputs.sh';
function observationInputs(raw) {
  const lines = raw.split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith('#'));
  assert.equal(lines.shift(), 'xperience_observation_swift_inputs=(');
  assert.equal(lines.pop(), ')');
  assert(lines.length > 0 && new Set(lines).size === lines.length, 'empty or duplicated observation inputs');
  for (const line of lines) {
    assert(/^app\/SourcesShared\/[A-Za-z0-9_/-]+\.swift$/.test(line) && !line.includes('..'),
      'unreviewed observation compiler input');
  }
  return lines;
}

// Copy the exact owning declaration, never reconstruct its type, isolation or initializer.
// An older baseline without any observation call needs no invented feature property.
function observationProperty(manager) {
  const declarations = manager.match(/^    nonisolated private static let xperienceExportObservation = XperienceExportObservationFence\(\)$/gm) ?? [];
  const mentions = manager.match(/^.*(?:let|var) xperienceExportObservation.*$/gm) ?? [];
  assert(mentions.length === declarations.length && declarations.length <= 1, 'observation declaration changed or is ambiguous');
  if (manager.includes('Self.xperienceExportObservation.')) {
    assert.equal(declarations.length, 1, 'production observation call has no owning declaration');
  }
  return declarations[0] ?? '';
}

module.exports = { observationInputList, observationInputs, observationProperty };
