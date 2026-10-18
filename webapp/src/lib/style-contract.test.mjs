import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// Source contracts for the shared chip definition; these do not measure browser
// rendering, profile tint contrast, glass composition, or device responsiveness.
const css = readFileSync(new URL('../styles/app.css', import.meta.url), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

function sharedChipRule() {
  const matches = [...css.matchAll(/\.btn-chip,\s*\.chip\s*\{([^}]+)\}/g)];
  assert.equal(matches.length, 1, 'Keep one authoritative shared chip base');
  return matches[0][1];
}

test('chip base styling has one shared owner instead of a discarded duplicate', () => {
  const shared = sharedChipRule();
  assert.equal([...css.matchAll(/(?:^|\})\s*\.chip\s*\{/g)].length, 0,
    'A standalone chip base duplicates the later design-system definition');
  for (const declaration of ['display: inline-flex', 'gap: 0.45rem',
    'padding: 0.62rem 1rem', 'background: var(--surface-2)', 'color: var(--text)']) {
    assert.ok(shared.includes(declaration), `Missing chip contract: ${declaration}`);
  }
});

test('disabled chips override selected, hover and press affordances together', () => {
  const selected = css.indexOf('.btn-chip.selected, .chip.selected');
  const disabled = /\.btn-chip:disabled,\s*\.chip:disabled\s*\{([^}]+)\}/.exec(css);
  assert.ok(selected >= 0 && disabled, 'Retain selected and shared disabled states');
  assert.ok(disabled.index > selected, 'Disabled styling must follow selected styling');
  for (const declaration of ['background: var(--surface-2)', 'color: var(--muted)',
    'box-shadow: none', 'cursor: default', 'transform: none']) {
    assert.ok(disabled[1].includes(declaration), `Missing disabled contract: ${declaration}`);
  }
});
