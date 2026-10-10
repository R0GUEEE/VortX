import { test } from "node:test";
import assert from "node:assert/strict";
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

const local = new Map();
const session = new Map();
const storage = (values) => ({
  get length() { return values.size; },
  key: (index) => [...values.keys()][index] ?? null,
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
});
globalThis.localStorage = storage(local);
globalThis.sessionStorage = storage(session);
globalThis.document = Object.assign(new EventTarget(), {
  visibilityState: "visible",
  documentElement: { dataset: {}, style: { setProperty() {}, removeProperty() {} } },
});
globalThis.window = new EventTarget();
globalThis.matchMedia = () => ({ matches: false });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: true } });
local.set("vortx.web.profiles.v1", JSON.stringify([{ id: "owner", name: "Owner" }]));
local.set("vortx.web.activeProfile.v1", "owner");

const realTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
const realNow = Date.now;
let now = 1_790_000_000_000;
let nextTimer = 0;
const timers = new Map();
globalThis.setTimeout = (fn, delay = 0) => {
  const id = ++nextTimer;
  timers.set(id, { fn, at: now + delay });
  return id;
};
globalThis.clearTimeout = (id) => timers.delete(id);
Date.now = () => now;
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => realTimeout(r, 1)); };
const until = async (predicate, message) => {
  for (let i = 0; i < 100 && !predicate(); i++) await new Promise((r) => realTimeout(r, 2));
  assert.ok(predicate(), message);
};
const advance = async (ms) => {
  const end = now + ms;
  for (;;) {
    const next = [...timers].filter(([, value]) => value.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
    if (!next) break;
    now = next[1].at;
    timers.delete(next[0]);
    next[1].fn();
    await settle();
  }
  now = end;
  await settle();
};

class FakeSocket {
  static instances = [];
  constructor(url) { this.url = url; this.closed = false; FakeSocket.instances.push(this); }
  open() { this.onopen?.(); }
  update(version) { this.onmessage?.({ data: JSON.stringify({ type: "updated", version }) }); }
  close() { this.closed = true; }
  disconnect() { this.closed = true; this.onclose?.(); }
}
globalThis.WebSocket = FakeSocket;

// Older TV browsers expose secure random bytes/WebCrypto but not randomUUID.
const nativeRandomUUID = Object.getOwnPropertyDescriptor(crypto, "randomUUID");
Object.defineProperty(crypto, "randomUUID", { configurable: true, value: undefined });
const vault = await import("./vault.ts");
let account = await import("./account.ts");
const store = await import("./store.ts");
const settings = await import("./settings.ts");
const profiles = await import("./profiles.ts");
const { sealDocument, openDocument } = vault.__syncCryptoTestHooks;
const json = (value, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "content-type": "application/json" },
});
let accountCount = 0;
const makeSession = () => {
  const id = `synthetic-sync-${++accountCount}`;
  return { token: `synthetic-token-${id}`, account: { id, email: `${id}@example.test`, username: id },
    dataKey: new Uint8Array(32).fill(accountCount) };
};
const legacyDoc = (accent = "vortx") => ({
  foreign: { preserve: true },
  vortx: { updatedAt: 10, profiles: [{ id: "remote-owner", name: "Owner", main: true,
    settings: { accent, playback: { audioLang: "en" } } }] },
});
const journalsFor = (s) => [...local.entries()].filter(([key, raw]) => {
  if (!key.startsWith("vortx.web.sync-intents.v1.") || key.endsWith(".owner")) return false;
  return JSON.parse(raw).accountId === s.account.id;
});

async function serverFor(s, initial = legacyDoc()) {
  let version = 20;
  let document;
  const requests = [];
  const puts = [];
  let nextGetGate;
  let nextPutGate;
  let failures = 0;
  let conditional = true;
  let activeGets = 0;
  let maxGets = 0;
  let activePuts = 0;
  let maxPuts = 0;
  const seal = async (doc) => sealDocument(s.dataKey, new TextEncoder().encode(JSON.stringify(doc)), s.account.id, version, false);
  document = await seal(initial);
  const fetch = async (input, init = {}) => {
    const url = String(input);
    if (!url.startsWith("https://api.vortx.tv/")) {
      return json({ id: "synthetic-addon", name: "Synthetic", resources: [], types: [], catalogs: [] });
    }
    assert.equal(init.headers.authorization, `Bearer ${s.token}`);
    assert.ok(!url.includes(s.token), "HTTP bearer never appears in a URL");
    requests.push({ method: init.method ?? "GET", headers: init.headers });
    if (init.method === "PUT") {
      activePuts++; maxPuts = Math.max(maxPuts, activePuts);
      const body = JSON.parse(init.body);
      puts.push(body);
      const gate = nextPutGate; nextPutGate = undefined;
      if (gate) await gate;
      activePuts--;
      if (failures) { failures--; return json({ error: "synthetic offline" }, 503); }
      const accepted = body.version > version;
      if (accepted) { version = body.version; document = body.document; }
      return json({ accepted });
    }
    activeGets++; maxGets = Math.max(maxGets, activeGets);
    const result = { document, version };
    const gate = nextGetGate; nextGetGate = undefined;
    if (gate) await gate;
    activeGets--;
    if (conditional && init.headers["if-none-match"] === `"${result.version}"`) return new Response(null, { status: 304 });
    return json(result);
  };
  globalThis.fetch = fetch;
  return {
    fetch, requests, puts,
    get version() { return version; },
    get maxGets() { return maxGets; },
    get maxPuts() { return maxPuts; },
    fail(count = 1) { failures = count; },
    conditional(value) { conditional = value; },
    holdGet() { let release; nextGetGate = new Promise((r) => { release = r; }); return release; },
    holdPut() { let release; nextPutGate = new Promise((r) => { release = r; }); return release; },
    async remote(doc) { version++; document = await seal(doc); },
    async read() {
      const value = await openDocument(s.dataKey, document, s.account.id, version);
      assert.ok(value); return JSON.parse(new TextDecoder().decode(value));
    },
  };
}

test("automatic encrypted account sync uses sockets, clocks, network and durable storage", async (t) => {
  let stop;
  const clean = () => {
    stop?.(); stop = undefined; account.signOut(); timers.clear();
    document.visibilityState = "visible"; navigator.onLine = true;
    profiles.setActiveProfile(profiles.profiles()[0].id);
  };
  t.after(() => {
    clean(); globalThis.setTimeout = realTimeout; globalThis.clearTimeout = realClearTimeout; Date.now = realNow;
    if (nativeRandomUUID) Object.defineProperty(crypto, "randomUUID", nativeRandomUUID);
    else delete crypto.randomUUID;
  });

  await t.test("socket bursts coalesce delayed pulls, polling is conditional and foreground/online catches up", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s);
    account.adoptSession(s); stop = account.startAccountSync();
    assert.match(session.get("vortx.web.sync-client.v1"), /^[0-9a-f]{32}$/, "journal IDs work without randomUUID");
    await until(() => account.accountSyncStatus().receivedVersion === 20, "initial account pull");
    const socket = FakeSocket.instances.at(-1);
    assert.equal(new URL(socket.url).searchParams.get("token"), s.token);
    assert.ok(![...local.values(), ...session.values()].some((v) => v.includes("/v1/sync/connect")), "socket URL is transient");
    const release = server.holdGet();
    await server.remote(legacyDoc("ocean")); socket.update(21);
    await server.remote(legacyDoc("royal"));
    for (let i = 0; i < 8; i++) socket.update(22);
    assert.equal(server.maxGets, 1, "only one receive GET may be active");
    release(); await until(() => account.accountSyncStatus().receivedVersion === 22, "coalesced follow-up pull");
    assert.equal(settings.getSettings().accentID, "royal"); assert.equal(server.puts.length, 0, "receive never echoes a PUT");
    await advance(3_000);
    assert.equal(server.requests.at(-1).headers["if-none-match"], '"22"');
    document.visibilityState = "hidden"; document.dispatchEvent(new Event("visibilitychange"));
    const count = server.requests.length; await server.remote(legacyDoc("gold")); await advance(6_000);
    assert.equal(server.requests.length, count, "hidden page does not poll"); assert.ok(socket.closed);
    document.visibilityState = "visible"; document.dispatchEvent(new Event("visibilitychange"));
    await until(() => settings.getSettings().accentID === "gold", "foreground catch-up");
    navigator.onLine = false; window.dispatchEvent(new Event("offline"));
    await server.remote(legacyDoc("ocean")); await advance(3_000);
    navigator.onLine = true; window.dispatchEvent(new Event("online"));
    await until(() => settings.getSettings().accentID === "ocean", "online catch-up");
    const old = FakeSocket.instances.at(-1); old.disconnect(); const before = FakeSocket.instances.length;
    await advance(999); assert.equal(FakeSocket.instances.length, before);
    await advance(1); assert.equal(FakeSocket.instances.length, before + 1, "socket reconnect uses backoff");
    server.conditional(false); await advance(3_000);
    assert.equal(settings.getSettings().accentID, "ocean", "older 200-only server fallback works");
  });

  await t.test("local settings survive an initial delayed GET and a PUT in flight, and retry without another edit", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s);
    account.adoptSession(s); const releaseGet = server.holdGet(); stop = account.startAccountSync();
    settings.updateSettings({ accentID: "gold" });
    assert.ok(journalsFor(s).length, "pre-hydration edit is durable immediately");
    releaseGet(); await settle(); assert.equal(settings.getSettings().accentID, "gold");
    const releasePut = server.holdPut(); server.fail(); await advance(800);
    await until(() => server.puts.length === 1, "held first PUT");
    settings.updateSettings({ accentID: "royal", background: "oled" });
    await server.remote(legacyDoc("crimson")); FakeSocket.instances.at(-1).update(server.version);
    await settle(); assert.equal(settings.getSettings().accentID, "royal", "pull cannot overwrite pending local delta");
    releasePut(); await settle(); await advance(1_000);
    await until(() => server.puts.length >= 2, "automatic failure retry");
    const doc = await server.read();
    assert.equal(doc.profileEdits.roster[0].settings.accent, "royal"); assert.equal(doc.profileEdits.roster[0].settings.oled, true);
    assert.deepEqual(doc.foreign, { preserve: true }); assert.equal(server.maxPuts, 1);
    assert.equal(journalsFor(s).length, 0, "ACK removes durable intent");
  });

  await t.test("add-on failure retries exact descriptor/removal intent and retains edits arriving during PUT", async () => {
    clean(); const s = makeSession(); const preserved = { transportUrl: "https://old.test/manifest.json", manifest: { id: "old" }, foreign: 7 };
    const server = await serverFor(s, { ...legacyDoc(), addons: [preserved], nativeSync: { future: "opaque" }, nativeHostPreferences: { keep: true } });
    account.adoptSession(s); account.applySyncDoc(legacyDoc());
    const url = "https://addons.test:443/A/../B/manifest.json?Token=AbC";
    await store.addAddon(url); store.removeAddon(preserved.transportUrl);
    const release = server.holdPut(); server.fail(); await advance(350);
    await until(() => server.puts.length === 1, "first addon PUT");
    const second = "https://addons.test:443/A/../B/manifest.json?Token=abc";
    await store.addAddon(second); release(); await settle(); await advance(1_000);
    await until(() => server.puts.length >= 2 && journalsFor(s).length === 0, "durable addon retry and follow-on edit");
    const doc = await server.read();
    assert.ok(doc.addons.some((a) => a.transportUrl === url)); assert.ok(doc.addons.some((a) => a.transportUrl === second));
    assert.ok(!doc.addons.some((a) => a.transportUrl === preserved.transportUrl));
    assert.deepEqual(doc.nativeSync, { future: "opaque" }); assert.deepEqual(doc.nativeHostPreferences, { keep: true });
    assert.equal(server.maxPuts, 1);
  });

  await t.test("progress captures owner/overlay scopes, failed settings cannot starve it, and inherited titles are not swept", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s, { foreign: true });
    account.adoptSession(s);
    local.set("vortx.web.cw.v1", JSON.stringify([{ id: "foreign-title", type: "movie", name: "Foreign", resumeId: "foreign-title", position: 10, duration: 100, updatedAt: now }]));
    settings.updateSettings({ accentID: "gold" }); // No main profile: settings must remain pending.
    store.recordProgress({ id: "owner-title", type: "movie", name: "Owner" }, 20, 100);
    const overlay = profiles.addProfile("Synthetic overlay", "🦊");
    store.recordProgress({ id: "overlay-title", type: "movie", name: "Overlay" }, 30, 100);
    await advance(1_000); const doc = await server.read();
    assert.deepEqual(doc.webProgress.owner.map((e) => e.id), ["owner-title"]);
    assert.deepEqual(doc.webProgress.byProfile[overlay.id].map((e) => e.id), ["overlay-title"]);
    assert.ok(journalsFor(s).some(([, raw]) => JSON.parse(raw).settings.accentID === "gold"), "missing-main settings remain queued");
    assert.ok(!JSON.stringify(doc).includes("foreign-title")); assert.equal(server.maxPuts, 1);
  });

  await t.test("reload/new tab recovers inactive journals but does not copy a live tab's pending snapshot", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s);
    account.adoptSession(s); account.applySyncDoc(legacyDoc());
    settings.updateSettings({ accentID: "gold" });
    const [key, raw] = journalsFor(s)[0];
    account.signOut(); timers.clear(); // Retire the old page/account; its queued intent remains durable.
    session.delete("vortx.web.sync-client.v1");
    vault.saveSession(s);
    const reloaded = await import(`./account.ts?reload=${s.account.id}`);
    assert.equal(reloaded.currentSession().account.id, s.account.id);
    await advance(350); await until(() => server.puts.length === 1, "new-tab retry from retained journal");
    assert.equal((await server.read()).profileEdits.roster[0].settings.accent, "gold");
    assert.equal(local.get(key), undefined); reloaded.signOut(); timers.clear(); account = reloaded;

    const liveKey = key + "-live";
    local.set(liveKey, raw);
    local.set(liveKey + ".owner", JSON.stringify({ id: "another-live-page", until: now + 30_000 }));
    account.adoptSession(s); await advance(800);
    assert.equal(server.puts.length, 1, "live owner's old snapshot cannot be copied and replayed");
    local.delete(liveKey); local.delete(liveKey + ".owner");
  });

  await t.test("account switch fences delayed GET/manifest completions and keeps A's journal away from B", async () => {
    clean(); const a = makeSession(); const first = await serverFor(a);
    account.adoptSession(a); const releaseGet = first.holdGet(); stop = account.startAccountSync();
    settings.updateSettings({ accentID: "gold" });
    let releaseManifest;
    const pendingManifest = new Promise((r) => { releaseManifest = r; });
    globalThis.fetch = (url, init) => String(url).startsWith("https://slow.test/") ? pendingManifest : first.fetch(url, init);
    const install = store.addAddon("https://slow.test/manifest.json");
    const rejected = assert.rejects(install, /Account changed/);
    const b = makeSession(); const second = await serverFor(b, legacyDoc("ocean"));
    account.adoptSession(b); releaseGet(); releaseManifest(json({ id: "slow", name: "Slow" }));
    await rejected; await until(() => account.accountSyncStatus().receivedVersion === 20, "account B catch-up");
    await advance(1_000);
    assert.equal(settings.getSettings().accentID, "ocean"); assert.equal(second.puts.length, 0);
    assert.ok(journalsFor(a).length); assert.equal(journalsFor(b).length, 0);
    assert.ok(!store.installedUrls().includes("https://slow.test/manifest.json"));
  });

  await t.test("native fields mark received separately from applied without causing a full-GET loop", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s, { ...legacyDoc(), nativeSync: { schemaVersion: 1, opaque: true } });
    account.adoptSession(s); stop = account.startAccountSync();
    await until(() => account.accountSyncStatus().receivedVersion === 20, "native encrypted document received");
    assert.deepEqual(account.accountSyncStatus(), { receivedVersion: 20, appliedVersion: undefined, projectionPending: true });
    await advance(3_000); assert.equal(server.requests.at(-1).headers["if-none-match"], '"20"');
    assert.equal(server.puts.length, 0);
  });

  await t.test("a stalled mutation times out and retries its durable intent without blocking the writer", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s);
    account.adoptSession(s); account.applySyncDoc(legacyDoc());
    let hung = false; let aborted = false;
    globalThis.fetch = (url, init = {}) => {
      if (!hung && String(url).endsWith("/v1/backup") && (init.method ?? "GET") === "GET") {
        hung = true;
        return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => {
          aborted = true; reject(new DOMException("Synthetic timeout", "AbortError"));
        }, { once: true }));
      }
      return server.fetch(url, init);
    };
    settings.updateSettings({ accentID: "royal" }); await advance(800);
    assert.ok(hung); assert.equal(server.puts.length, 0);
    await advance(15_000); assert.ok(aborted, "mutation GET has a bounded deadline");
    assert.ok(journalsFor(s).length, "timeout never drops the queued edit");
    await advance(1_000); await until(() => server.puts.length === 1, "automatic retry after timeout");
    assert.equal((await server.read()).profileEdits.roster[0].settings.accent, "royal");
    assert.equal(journalsFor(s).length, 0);
  });

  await t.test("a resumed page retires its old snapshot after another page recovered and ACKed the journal", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s);
    account.adoptSession(s); account.applySyncDoc(legacyDoc());
    settings.updateSettings({ accentID: "gold" });
    const [key] = journalsFor(s)[0];
    timers.clear(); // The original page is suspended before its debounce fires.
    local.set(key + ".owner", JSON.stringify({ id: "recovering-page", until: now + 30_000 }));
    await vault.mutateSyncDoc(s, (doc) => { doc.vortx.profiles[0].settings.accent = "gold"; });
    await vault.mutateSyncDoc(s, (doc) => { doc.vortx.profiles[0].settings.accent = "royal"; });
    local.delete(key);
    local.set(key + ".owner", JSON.stringify({ id: "recovering-page", until: 0 }));
    stop = account.startAccountSync();
    await until(() => settings.getSettings().accentID === "royal", "resumed page applies the newest ACKed value");
    await advance(3_000);
    assert.equal(server.puts.length, 2, "the original gold snapshot cannot replay after ownership cleanup");
    assert.equal(journalsFor(s).length, 0);
  });

  await t.test("active fallback polling recovers a crashed page's journal when its lease expires", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s);
    const key = `vortx.web.sync-intents.v1.${s.account.id}.synthetic-crashed-page`;
    local.set(key, JSON.stringify({ accountId: s.account.id, editedAt: now, revision: 1,
      settings: { accentID: "royal" }, addons: {}, progress: {} }));
    local.set(key + ".owner", JSON.stringify({ id: "synthetic-crashed-page", until: now + 5_000 }));
    account.adoptSession(s); stop = account.startAccountSync(); await settle();
    await advance(3_000); assert.equal(server.puts.length, 0, "unexpired ownership is respected");
    await advance(3_350); await until(() => server.puts.length === 1, "expired orphan automatically recovered");
    assert.equal((await server.read()).profileEdits.roster[0].settings.accent, "royal");
    assert.equal(settings.getSettings().accentID, "royal"); assert.equal(local.get(key), undefined);
  });

  await t.test("an owner-library-only receive signals a live UI refresh without echoing an upload", async () => {
    clean(); const s = makeSession(); const server = await serverFor(s);
    account.adoptSession(s); stop = account.startAccountSync(); await settle();
    let changes = 0; const changed = () => changes++;
    window.addEventListener("vortx:addons-changed", changed);
    const doc = legacyDoc(); doc.vortx.library = [{ id: "remote-library-only", type: "movie", name: "Synced title" }];
    await server.remote(doc); FakeSocket.instances.at(-1).update(server.version);
    await until(() => changes === 1, "owner library UI refresh event");
    window.removeEventListener("vortx:addons-changed", changed);
    assert.ok(store.libraryItems().some((item) => item.id === "remote-library-only"));
    assert.equal(server.puts.length, 0);
  });
});
