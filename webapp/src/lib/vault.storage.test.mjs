import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { registerHooks } from "node:module";

registerHooks({ resolve(specifier, context, next) {
  if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier) && context.parentURL) {
    const candidate = new URL(specifier + ".ts", context.parentURL);
    if (existsSync(fileURLToPath(candidate))) return { url: candidate.href, shortCircuit: true };
  }
  return next(specifier, context);
} });
const vault = await import("./vault.ts");
class Storage {
  data = new Map();
  getItem(key) { return this.data.get(key) ?? null; }
  setItem(key, value) { this.data.set(key, String(value)); }
  removeItem(key) { this.data.delete(key); }
}
function environment() {
  Object.assign(globalThis, { localStorage: new Storage(), sessionStorage: new Storage(), window: new EventTarget() });
}
const session = (id = "synthetic-a", byte = 3, token = "synthetic-token-a") => ({
  token, account: { id, username: id, email: id + "@example.invalid" }, dataKey: new Uint8Array(32).fill(byte),
});
const descriptor = (s) => JSON.stringify({ token: s.token, account: s.account });

test("owned tab key round-trips without persisting the data key in local storage", () => {
  environment(); const s = session(); vault.saveSession(s);
  assert.deepEqual(vault.loadSession(), s);
  assert.deepEqual(JSON.parse(localStorage.getItem("vortx.session.v1")), { token: s.token, account: s.account });
  const envelope = JSON.parse(sessionStorage.getItem("vortx.dk.v2"));
  assert.deepEqual(envelope, { schemaVersion: 2, accountId: s.account.id, token: s.token, dataKey: Buffer.from(s.dataKey).toString("base64") });
});

test("shared descriptor replacement cannot attach another account or token to the current tab key", () => {
  for (const next of [session("synthetic-b", 7, "synthetic-token-b"), session("synthetic-a", 7, "synthetic-token-b")]) {
    environment(); const first = session(); vault.saveSession(first);
    localStorage.setItem("vortx.session.v1", descriptor(next));
    assert.equal(vault.loadSession(), null);
    assert.equal(localStorage.getItem("vortx.session.v1"), descriptor(next), "rejecting this tab must preserve the other tab's descriptor");
  }
});

test("unbound legacy tab keys fail closed even beside an apparently valid descriptor", () => {
  environment(); const s = session();
  localStorage.setItem("vortx.session.v1", descriptor(s));
  sessionStorage.setItem("vortx.dk.v1", Buffer.from(s.dataKey).toString("base64"));
  assert.equal(vault.loadSession(), null);
  assert.equal(sessionStorage.getItem("vortx.dk.v2"), null);
});

test("embedded legacy key migrates only from its own descriptor and ignores an unrelated bare tab key", () => {
  environment(); const s = session();
  localStorage.setItem("vortx.session.v1", JSON.stringify({ token: s.token, account: s.account, dataKey: Buffer.from(s.dataKey).toString("base64") }));
  sessionStorage.setItem("vortx.dk.v1", Buffer.alloc(32, 9).toString("base64"));
  assert.deepEqual(vault.loadSession(), s);
  assert.deepEqual(JSON.parse(localStorage.getItem("vortx.session.v1")), { token: s.token, account: s.account });
  assert.equal(sessionStorage.getItem("vortx.dk.v1"), null);
  assert.deepEqual(vault.loadSession(), s);
});

test("malformed or mismatched versioned envelopes never fall back to an embedded or bare key", () => {
  for (const invalid of [
    "not json",
    JSON.stringify({ schemaVersion: 1, accountId: "synthetic-a", token: "synthetic-token-a", dataKey: Buffer.alloc(32, 3).toString("base64") }),
    JSON.stringify({ schemaVersion: 2, accountId: "synthetic-b", token: "synthetic-token-a", dataKey: Buffer.alloc(32, 3).toString("base64") }),
    JSON.stringify({ schemaVersion: 2, accountId: "synthetic-a", token: "synthetic-token-a", dataKey: Buffer.alloc(16, 3).toString("base64") }),
  ]) {
    environment(); const s = session();
    localStorage.setItem("vortx.session.v1", JSON.stringify({ token: s.token, account: s.account, dataKey: Buffer.from(s.dataKey).toString("base64") }));
    sessionStorage.setItem("vortx.dk.v2", invalid);
    sessionStorage.setItem("vortx.dk.v1", Buffer.from(s.dataKey).toString("base64"));
    assert.equal(vault.loadSession(), null);
  }
});

test("a failed tab-key save does not publish a replacement shared descriptor", () => {
  environment(); const first = session(), next = session("synthetic-b", 7, "synthetic-token-b"); vault.saveSession(first);
  const original = sessionStorage.setItem.bind(sessionStorage);
  sessionStorage.setItem = () => { throw new Error("synthetic storage unavailable"); };
  vault.saveSession(next);
  sessionStorage.setItem = original;
  assert.equal(localStorage.getItem("vortx.session.v1"), descriptor(first));
  assert.deepEqual(vault.loadSession(), first);
});

test("session save, legacy migration and logout preserve every account's rollback ratchets", () => {
  environment(); const s = session();
  for (const id of [s.account.id, "synthetic-other"]) {
    localStorage.setItem("vortx.syncVer." + id, "100");
    localStorage.setItem("vortx.sawV2." + id, "1");
  }
  localStorage.setItem("vortx.session.v1", JSON.stringify({ token: s.token, account: s.account, dataKey: Buffer.from(s.dataKey).toString("base64") }));
  assert.deepEqual(vault.loadSession(), s, "owned legacy migration also preserves ratchets");
  vault.saveSession(s); sessionStorage.setItem("vortx.dk.v1", "synthetic retired key"); vault.clearSession();
  assert.equal(vault.loadSession(), null);
  assert.equal(sessionStorage.getItem("vortx.dk.v1"), null);
  assert.equal(sessionStorage.getItem("vortx.dk.v2"), null);
  for (const id of [s.account.id, "synthetic-other"]) {
    assert.equal(localStorage.getItem("vortx.syncVer." + id), "100");
    assert.equal(localStorage.getItem("vortx.sawV2." + id), "1");
  }
});
