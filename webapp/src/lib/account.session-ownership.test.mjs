import assert from "node:assert/strict";
import { test } from "node:test";
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
const storage = () => {
  const values = new Map();
  return { values, get length() { return values.size; }, key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key) };
};
globalThis.localStorage = storage();
globalThis.sessionStorage = storage();
globalThis.window = new EventTarget();
globalThis.document = Object.assign(new EventTarget(), { visibilityState: "visible",
  documentElement: { dataset: {}, style: { setProperty() {}, removeProperty() {} } } });
globalThis.matchMedia = () => ({ matches: false });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: true } });
class FakeSocket { static instances = []; constructor() { FakeSocket.instances.push(this); } close() { this.closed = true; } }
globalThis.WebSocket = FakeSocket;
const vault = await import("./vault.ts");
const account = await import("./account.ts");
const settings = await import("./settings.ts");
const makeSession = (id, byte = 1) => ({ token: "synthetic-token-" + id,
  account: { id, username: id, email: id + "@example.invalid" }, dataKey: new Uint8Array(32).fill(byte) });
const descriptor = (session) => JSON.stringify({ token: session.token, account: session.account });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const storageEvent = () => window.dispatchEvent(Object.assign(new Event("storage"), { key: "vortx.session.v1" }));
const cleanup = () => { account.signOut(); localStorage.values.clear(); sessionStorage.values.clear(); };

// Only synthetic storage and mocked fetch are used. No authentication/account requests leave this process.
test("a delayed validation cannot replace or delete a different tab's session descriptor", async () => {
  for (const status of [200, 401]) {
    cleanup(); const a = makeSession("validate-a-" + status), b = makeSession("validate-b-" + status, 2);
    vault.saveSession(a); account.adoptSession(a);
    let release; globalThis.fetch = async () => new Promise((resolve) => { release = resolve; });
    const pending = account.ensureValidSession();
    const tabKey = sessionStorage.getItem("vortx.dk.v2");
    localStorage.setItem("vortx.session.v1", descriptor(b));
    release(Response.json({ account: a.account }, { status }));
    assert.equal(await pending, null, "the tab cannot adopt B without B's tab key");
    assert.equal(account.currentSession(), null);
    assert.equal(localStorage.getItem("vortx.session.v1"), descriptor(b));
    assert.equal(sessionStorage.getItem("vortx.dk.v2"), tabKey, "retirement does not delete the tab key or another tab's descriptor");
  }
  cleanup();
});

test("validation keeps a new same-tab adoption and still persists owned account refreshes", async () => {
  cleanup(); const a = makeSession("adopt-a"), b = makeSession("adopt-b", 2);
  vault.saveSession(a); account.adoptSession(a);
  let release; globalThis.fetch = async () => new Promise((resolve) => { release = resolve; });
  const pending = account.ensureValidSession();
  vault.saveSession(b); account.adoptSession(b);
  release(Response.json({ account: a.account }));
  assert.equal((await pending).account.id, b.account.id);
  assert.equal(vault.loadSession().account.id, b.account.id);
  globalThis.fetch = async () => Response.json({ account: { ...b.account, twoFactorEnabled: true } });
  assert.equal((await account.ensureValidSession()).account.twoFactorEnabled, true);
  assert.equal(vault.loadSession().account.twoFactorEnabled, true);
  cleanup();
});

test("a cross-tab replacement fences delayed receives, retires sockets, and preserves durable edits", async () => {
  cleanup(); const a = makeSession("receive-a"), b = makeSession("receive-b", 2);
  vault.saveSession(a); account.adoptSession(a);
  const doc = { apiKeys: { tmdb: "synthetic-old-account-value" }, vortx: { updatedAt: 1,
    profiles: [{ id: "owner", name: "Owner", main: true, settings: {} }] } };
  const ciphertext = await vault.__syncCryptoTestHooks.sealDocument(a.dataKey,
    new TextEncoder().encode(JSON.stringify(doc)), a.account.id, 20, false);
  const entered = deferred(), response = deferred();
  globalThis.fetch = async () => { entered.resolve(); return response.promise; };
  const stop = account.startAccountSync(), pull = account.hydrateFromAccount(a);
  try {
    await entered.promise;
    settings.updateSettings({ tmdbKey: "synthetic-pending-edit" });
    const journal = [...localStorage.values].find(([key, value]) => key.startsWith("vortx.web.sync-intents.v1.") && !key.endsWith(".owner") && JSON.parse(value).accountId === a.account.id);
    assert.ok(journal, "explicit edit was journaled before any network result");
    const currentValue = settings.getSettings().tmdbKey;
    localStorage.setItem("vortx.session.v1", descriptor(b));
    response.resolve(Response.json({ document: ciphertext, version: 20 })); await pull;
    assert.equal(settings.getSettings().tmdbKey, currentValue, "pending old-account read cannot apply before storage event delivery");
    assert.equal(localStorage.getItem("vortx.syncVer." + a.account.id), null, "the delayed read cannot advance account floors");
    storageEvent();
    assert.equal(account.currentSession(), null);
    assert.ok(FakeSocket.instances.at(-1).closed);
    assert.equal(localStorage.getItem("vortx.session.v1"), descriptor(b));
    assert.equal(localStorage.getItem(journal[0]), journal[1], "unsent old-account edit remains durable under its original account");
  } finally {
    response.resolve(Response.json({ document: ciphertext, version: 20 })); stop(); await pull; cleanup();
  }
});

test("fallback hydration also fences storage changes without starting automatic sync", async () => {
  cleanup(); const a = makeSession("hydrate-a"), b = makeSession("hydrate-b", 2);
  vault.saveSession(a); account.adoptSession(a);
  const ciphertext = await vault.__syncCryptoTestHooks.sealDocument(a.dataKey,
    new TextEncoder().encode(JSON.stringify({ apiKeys: { tmdb: "synthetic-old-account-value" } })), a.account.id, 20, false);
  const entered = deferred(), response = deferred();
  globalThis.fetch = async () => { entered.resolve(); return response.promise; };
  const pending = account.hydrateFromAccount(a);
  try {
    await entered.promise;
    localStorage.setItem("vortx.session.v1", descriptor(b));
    response.resolve(Response.json({ document: ciphertext, version: 20 })); await pending;
    assert.notEqual(settings.getSettings().tmdbKey, "synthetic-old-account-value");
    assert.equal(localStorage.getItem("vortx.syncVer." + a.account.id), null);
    assert.equal(localStorage.getItem("vortx.session.v1"), descriptor(b));
  } finally { response.resolve(Response.json({ document: ciphertext, version: 20 })); await pending; cleanup(); }
  const retired = makeSession("hydrate-removed-before-start"); vault.saveSession(retired); account.adoptSession(retired);
  localStorage.removeItem("vortx.session.v1"); let requests = 0;
  globalThis.fetch = async () => { requests++; throw Error("unexpected retired-account read"); };
  await account.hydrateFromAccount(retired);
  assert.equal(requests, 0, "hydration retains adoption-time ownership even when deletion preceded the call");
  assert.equal(localStorage.getItem("vortx.syncVer." + retired.account.id), null);
  cleanup();
});

test("sync start reconciles a descriptor change that occurred before its storage listener", () => {
  cleanup(); const a = makeSession("start-a"), b = makeSession("start-b", 2);
  vault.saveSession(a); account.adoptSession(a);
  localStorage.setItem("vortx.session.v1", descriptor(b));
  let calls = 0; globalThis.fetch = async () => { calls++; throw Error("unexpected old-account request"); };
  const stop = account.startAccountSync();
  try { assert.equal(account.currentSession(), null); assert.equal(calls, 0); assert.equal(localStorage.getItem("vortx.session.v1"), descriptor(b)); }
  finally { stop(); cleanup(); }
});

test("queued edits retain adoption-time ownership when the descriptor disappears before the storage event", async () => {
  cleanup(); const a = makeSession("queued-delete-a"); vault.saveSession(a); account.adoptSession(a);
  let gets = 0, puts = 0;
  globalThis.fetch = async (_url, init) => { if (init.method === "PUT") { puts++; return Response.json({ accepted: true }); } gets++; return Response.json({}, { status: 404 }); };
  settings.updateSettings({ tmdbKey: "synthetic-retained-intent" });
  try {
    const journal = [...localStorage.values].find(([key, value]) => key.startsWith("vortx.web.sync-intents.v1.") && !key.endsWith(".owner") && JSON.parse(value).accountId === a.account.id);
    assert.ok(journal); localStorage.removeItem("vortx.session.v1");
    await new Promise((resolve) => setTimeout(resolve, 900));
    assert.equal(gets, 0); assert.equal(puts, 0); assert.equal(localStorage.getItem(journal[0]), journal[1]);
  } finally { cleanup(); }
});
