import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { escapeHtml } from "./dom.ts";

// Proposed destination: webapp/src/lib/dom-xss-regression.test.mjs.
// Follow dom.css-image.test.mjs: execute the production markup, avoiding player/transport side effects.
function sourcePart(path, start, end) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const offset = source.indexOf(start), limit = source.indexOf(end, offset);
  assert.ok(offset >= 0 && limit > offset, "production extraction markers remain exact");
  return source.slice(offset, limit);
}

const controlsSource = sourcePart("./playerControls.ts", "export function mountControls(", "  const $ =");
function controlsShell(ctx) {
  const title = { textContent: "" };
  const stage = { innerHTML: "", querySelector: (selector) => selector === "#pl-title" ? title : null };
  const document = { createElement: () => stage };
  const js = stripTypeScriptTypes(controlsSource.replace(/^export /, "") + "\nreturn stageRoot;\n}", { mode: "strip" });
  const mount = Function("document", "icon", "escapeHtml", js + ";return mountControls;")(document, () => "", escapeHtml);
  mount({ appendChild() {} }, {}, ctx);
  return { stage, title };
}

const toggleSource = sourcePart("../views/settings.ts", "function toggle(", "/** A -/+ stepper");
const toggle = Function(stripTypeScriptTypes(toggleSource, { mode: "strip" }) + ";return toggle;")();
const attack = '\"><img data-dom-xss-probe src=x onerror="globalThis.__domXss = true"><span data-x="';

test("player skip labels encode runtime persisted values in both text and quoted attributes", () => {
  const { stage } = controlsShell({ title: "Fixture", skipStep: attack });
  assert.equal(stage.innerHTML.includes("<img data-dom-xss-probe"), false);
  assert.equal(stage.innerHTML.includes("&lt;img data-dom-xss-probe"), true);
  assert.equal((stage.innerHTML.match(/<small>[^<]*<\/small>/g) ?? []).length, 2);
  assert.equal((stage.innerHTML.match(/aria-label="Skip (?:back|forward) [^"]* seconds"/g) ?? []).length, 2);
});

test("player retains ordinary numeric skip labels and plain-text add-on titles", () => {
  for (const skipStep of [10, 15, 30]) {
    const { stage, title } = controlsShell({ title: attack, skipStep });
    assert.ok(stage.innerHTML.includes(`aria-label="Skip back ${skipStep} seconds"`));
    assert.ok(stage.innerHTML.includes(`aria-label="Skip forward ${skipStep} seconds"`));
    assert.equal((stage.innerHTML.match(new RegExp(`<small>${skipStep}</small>`, "g")) ?? []).length, 2);
    assert.equal(stage.innerHTML.includes(attack), false);
    assert.equal(title.textContent, attack);
  }
});

test("settings switch attributes use boolean literals for hostile runtime persisted values", () => {
  const markup = toggle("toggle-alerts", attack);
  assert.equal(markup.includes("data-dom-xss-probe"), false);
  assert.ok(markup.includes('class="switch on"'));
  assert.ok(markup.includes('aria-checked="true"'));
});

test("settings switches retain ordinary true and false states", () => {
  assert.ok(toggle("toggle-alerts", true).includes('class="switch on"'));
  assert.ok(toggle("toggle-alerts", true).includes('aria-checked="true"'));
  assert.ok(toggle("toggle-alerts", false).includes('class="switch"'));
  assert.ok(toggle("toggle-alerts", false).includes('aria-checked="false"'));
});
