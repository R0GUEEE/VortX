// Account/session sync for the webapp, read by the nav, Login and Settings. It owns:
//   - the single in-memory session cache (so callers don't re-read localStorage on every render),
//   - the boot-time validation (loadSession then GET /v1/auth/me, clearing on a definite 401),
//   - sign-out and a subscribe/notify bus,
//   - bounded socket/conditional-poll receive and foreground catch-up, and
//   - an account-scoped journal of explicit edits, ACKed only after serialized successful writes.
// vault.ts owns session persistence and encrypted transport. Journals carry no session/key/socket URL.

import { loadSession, clearSession, validateSession, saveSession, getSyncDoc, fetchSync, connectSyncSocket, mutateSyncDoc, type Session } from "./vault";
import {
  mergeInstalledAddons,
  pruneInstalledAddons,
  applyAddonOrder,
  registerAddonsSyncPusher,
  registerCwSyncPusher,
  webProgressEntries,
  webProgressTombstones,
  installedUrls,
  mergeLibrary,
  mergeContinueWatching,
  mergeLibraryForScope,
  mergeContinueWatchingForScope,
  mergeContinueWatchingTombstonesForScope,
  mergeCWTombstones,
  type CWEntry,
  type CWTombstone,
  type WebProgressEntry,
} from "./store";
import {
  mergeSyncedProfiles,
  profiles as localProfiles,
  activeProfileId,
  isOwnerProfile,
  onProfilesChange,
  type SyncedProfile,
} from "./profiles";
import { getSettings, updateSettings, onSettingsChange, type Settings } from "./settings";
import {
  settingsPatchFromDoc,
  mergeWebappSettingsPatchIntoProfile,
  effectiveMainSettings,
  mainProfileId,
} from "./syncSettings";
import { CINEMETA_URL, loadAddon } from "./addon";
import type { MetaItem } from "./types";
import { effectiveAddonRemovals, applyAddonIntents } from "./addonRemovals";

// The in-memory cache. `undefined` = not yet hydrated from storage; `null` = hydrated, signed out.
// We lazily hydrate from loadSession() on first read so a hard reload restores the signed-in state.
let cached: Session | null | undefined = undefined;

type Listener = (session: Session | null) => void;
const listeners = new Set<Listener>();

/** Set the cache and tell every subscriber. The single mutation point for the session. */
function setSession(next: Session | null): void {
  bindSettingsSyncState(next);
  cached = next;
  bindReceiveSession(next);
  notify();
}

/** The current session (cached). Hydrates from localStorage on first call. This is a SYNCHRONOUS
 *  best-effort read: it does not validate the token with the server (use ensureValidSession on boot
 *  for that), so a revoked token still reads as signed-in until the next validation. */
export function currentSession(): Session | null {
  if (cached === undefined) {
    cached = loadSession();
    bindSettingsSyncState(cached);
    bindReceiveSession(cached);
  }
  return cached;
}

/** Whether there is a stored session on this device (best-effort, not server-validated). */
export function isSignedIn(): boolean {
  return currentSession() !== null;
}

/** The label for the signed-in chip: the username if present, else the email, else null. */
export function accountDisplay(): string | null {
  const s = currentSession();
  if (!s) return null;
  return s.account.username || s.account.email || null;
}

/** Boot guard: hydrate the session, then validate it once with the server (GET /v1/auth/me). On a
 *  definite 401 (token revoked/expired) the session is cleared; a network blip keeps it (validateSession
 *  is lenient). On success it refreshes account fields and re-persists. Returns the live session or
 *  null. Safe to call once at startup, before painting the nav. */
export async function ensureValidSession(): Promise<Session | null> {
  const session = loadSession();
  const prior = currentSession();
  if (!session) {
    setSession(null);
    return null;
  }
  const ok = await validateSession(session);
  if (!sameSession(currentSession(), prior)) return currentSession();
  if (!ok) {
    clearSession();
    setSession(null);
    return null;
  }
  // validateSession refreshes account fields (e.g. twoFactorEnabled) in place; re-persist them.
  saveSession(session);
  setSession(session);
  return session;
}

/** Adopt a freshly created session (called by the Login screen after register/login/recover/reset).
 *  vault already persisted it via saveSession; this updates the cache + notifies the UI. */
export function adoptSession(session: Session): void {
  setSession(session);
}

/** Pull a string transport URL out of a synced add-on entry (the app emits {transportUrl,name}; the web
 *  Stremio import emits plain strings or {transportUrl}). */
function addonUrl(a: unknown): string | null {
  if (typeof a === "string") return a;
  if (a && typeof a === "object" && typeof (a as { transportUrl?: unknown }).transportUrl === "string") {
    return (a as { transportUrl: string }).transportUrl;
  }
  return null;
}

/** Coerce an unknown to an array of plain objects (the loose synced-doc shape). */
function asObjArr(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v) ? (v.filter((x) => x && typeof x === "object") as Record<string, unknown>[]) : [];
}

/** Map synced library items (carrying t/d seconds + lastWatched + v resumeId) to CW entries. Shared by
 *  the owner library and each per-profile (byProfile) library so the logic stays in one place. */
function cwEntriesFrom(items: unknown): CWEntry[] {
  const arr = asObjArr(items);
  const out: CWEntry[] = [];
  arr.forEach((it, i) => {
    if (typeof it.id !== "string" || typeof it.type !== "string" || typeof it.name !== "string") return;
    const t = Number(it.t);
    const d = Number(it.d);
    if (!(t > 0) || !(d > 0) || t / d >= 0.95) return; // not started / already finished
    // lastWatched may be an ISO string OR an epoch (seconds or ms). Missing/unparseable clocks retain
    // source order in the non-positive range. Never synthesize wall-clock freshness: doing that would
    // make an undated stale account row newer than a real local removal tombstone.
    let updatedAt = NaN;
    if (typeof it.lastWatched === "string") updatedAt = Date.parse(it.lastWatched);
    else if (typeof it.lastWatched === "number") updatedAt = it.lastWatched < 1e12 ? it.lastWatched * 1000 : it.lastWatched;
    if (!Number.isFinite(updatedAt) || updatedAt <= 0) updatedAt = -i;
    out.push({
      id: it.id,
      type: it.type,
      name: it.name,
      poster: typeof it.poster === "string" ? it.poster : undefined,
      resumeId: typeof it.v === "string" && it.v ? it.v : it.id, // overlay items carry the resume episode id
      position: t,
      duration: d,
      updatedAt,
    });
  });
  return out;
}

/** Build the web profile roster from the synced doc, mirroring the dashboard's normalizeDoc essentials:
 *  read vortx.profiles, collapse duplicate "main" rows (the duplicate-Main bug), drop deletedProfiles
 *  tombstones unconditionally, and overlay doc.profileEdits ONLY when it is newer than the mirror
 *  (editedAt > vortx.updatedAt) so a stale web edit never masks a newer device change. Returns the slim
 *  roster the web store needs. */
function rosterFromDoc(vortx: Record<string, unknown>, doc: Record<string, unknown>): SyncedProfile[] {
  let roster: SyncedProfile[] = asObjArr(vortx.profiles)
    .filter((p) => p.id != null)
    .map((p, i) => {
      const st = p.settings && typeof p.settings === "object" ? (p.settings as Record<string, unknown>) : {};
      const avatar = typeof st.avatar === "string" && st.avatar.trim() ? st.avatar.trim() : undefined;
      return { id: String(p.id), name: String(p.name ?? `Profile ${i + 1}`), main: !!p.main || i === 0, avatar };
    });
  const mains = roster.filter((p) => p.main);
  if (mains.length > 1) {
    const drop = new Set(mains.slice(1).map((p) => p.id));
    roster = roster.filter((p) => !drop.has(p.id));
  }
  const tombIds = new Set((Array.isArray(vortx.deletedProfiles) ? vortx.deletedProfiles : []).map((x) => String(x)));
  if (tombIds.size) roster = roster.filter((p) => !tombIds.has(p.id));
  const edits = doc.profileEdits && typeof doc.profileEdits === "object" ? (doc.profileEdits as Record<string, unknown>) : null;
  if (edits && (Number(edits.editedAt) || 0) > (Number(vortx.updatedAt) || 0) && Array.isArray(edits.roster)) {
    const byId = new Map(roster.map((p) => [p.id, p] as const));
    for (const raw of edits.roster) {
      if (!raw || typeof raw !== "object") continue;
      const e = raw as Record<string, unknown>;
      if (e.id == null) continue;
      const id = String(e.id);
      if (tombIds.has(id)) continue;
      if (e.deleted) { byId.delete(id); continue; }
      const base = byId.get(id);
      if (base) byId.set(id, { ...base, name: typeof e.name === "string" && e.name ? e.name : base.name });
      else byId.set(id, { id, name: String(e.name ?? "Profile"), main: false });
    }
    roster = [...byId.values()];
  }
  return roster;
}

/** Apply a decrypted sync document to local state (READ-ONLY merge: add-ons, library, metadata keys).
 *  Pure (no network) so it is unit-testable; hydrateFromAccount fetches the doc then calls this. Tolerant
 *  of any missing/odd key - a partial or foreign doc never throws. */
export function applySyncDoc(doc: Record<string, unknown> | null | undefined): void {
  if (!doc || typeof doc !== "object") return;
  discardTransferredIntents(settingsSyncState);
  withSuppressedUp(() => applySyncDocLocally(doc));
}

function applySyncDocLocally(doc: Record<string, unknown>): void {
  const vortx = (doc.vortx && typeof doc.vortx === "object" ? doc.vortx : {}) as Record<string, unknown>;

  // Settings read-down: metadata API keys (doc.apiKeys) + the app/dashboard per-profile appearance,
  // playback and stream-filter settings (doc.vortx.profiles[main].settings / doc.profileEdits). Merge
  // them into the owner's complete snapshot. Apply that snapshot live only while the owner is active:
  // account hydration must not replace an overlay profile's local look. The live application is wrapped
  // in suppressUp so hydration cannot bounce back up as a web-authored change (see the write-up below).
  const keys = (doc.apiKeys && typeof doc.apiKeys === "object" ? doc.apiKeys : {}) as Record<string, unknown>;
  const metadataPatch: Partial<Settings> = {
    tmdbKey: typeof keys.tmdb === "string" ? keys.tmdb : "",
    mdblistKey: typeof keys.mdblist === "string" ? keys.mdblist : "",
  };
  const settingsPatch = settingsPatchFromDoc(doc);
  const ownerPatch: Partial<Settings> = { ...metadataPatch, ...settingsPatch };
  // A pull can race a local edit or failed PUT. Keep that edit live until its durable intent is ACKed.
  for (const key of settingsSyncState.pendingKeys) {
    if (settingsSyncState.pendingSettings) Object.assign(ownerPatch, { [key]: settingsSyncState.pendingSettings[key] });
  }
  if (Object.keys(ownerPatch).length) {
    const ownerSettings = mergeOwnerSettings(ownerPatch);
    if (isOwnerProfile(activeProfileId())) {
      withSuppressedUp(() => updateSettings(ownerSettings));
    } else {
      const sharedPatch = sharedSettingsPatch(ownerPatch);
      if (Object.keys(sharedPatch).length) {
        withSuppressedUp(() => updateSettings(sharedPatch));
      }
    }
  }

  // Add-ons: the app summary (vortx.addons: [{transportUrl,name}]) + the web Stremio import (doc.addons).
  // Membership union first (never drops a local add-on), then apply the synced ORDER (vortx order wins,
  // Cinemeta stays pinned first).
  const urls: string[] = [];
  for (const a of Array.isArray(vortx.addons) ? vortx.addons : []) {
    const u = addonUrl(a);
    if (u) urls.push(u);
  }
  for (const a of Array.isArray(doc.addons) ? doc.addons : []) {
    const u = addonUrl(a);
    if (u) urls.push(u);
  }
  // Removal set: a URL deleted on ANY surface. mergeInstalledAddons only ever UNIONs, so without subtracting
  // this a deleted add-on still in vortx.addons (app channel), a Stremio re-import, or one removed on an
  // Apple device would be added straight back - THAT is the "I removed YouTube/Watch Hub but it keeps coming
  // back" bug. Three sources, all folded (matching the app's own union): doc.removedAddons keys (this web
  // stack's tombstones) + doc.webAddonRemovals (the flat mirror) + doc.vortx.deletedAddons (removals done in
  // the Apple app). Normalize (trim+lowercase) BOTH sides so casing differences can't cause a miss - the app
  // keys its tombstones the same way. Re-adding a URL clears the web tombstone (add wins LWW).
  const removedNorm = effectiveAddonRemovals(doc);
  for (const intent of Object.values(settingsSyncState.addonIntents)) {
    const normalized = normalizeAddonUrl(intent.url);
    if (intent.added) { removedNorm.delete(normalized); urls.push(intent.url); }
    else removedNorm.add(normalized);
  }
  const liveUrls = removedNorm.size ? urls.filter((u) => !removedNorm.has(normalizeAddonUrl(u))) : urls;
  const addedChanged = mergeInstalledAddons(liveUrls);
  const toPrune = removedNorm.size ? installedUrls().filter((u) => removedNorm.has(normalizeAddonUrl(u))) : [];
  const prunedChanged = toPrune.length ? pruneInstalledAddons(toPrune) : false;
  const addonsChanged = addedChanged || prunedChanged;
  const orderChanged = applyAddonOrder(liveUrls);

  // Owner library (vortx.library: [{id,name,type,poster,t,d,lastWatched,v,...}]). The app emits t/d in
  // seconds (the dashboard + now the web app derive Continue Watching from each item's progress).
  const libItems = (Array.isArray(vortx.library) ? vortx.library : []) as Array<Record<string, unknown>>;
  const libraryChanged = mergeLibrary(libItems as unknown as MetaItem[]);

  // Apply bilateral timestamped removals BEFORE any live union so stale account/app rows cannot resurrect
  // a clear or >=95%-finished title. Older documents simply omit webProgress.removed.
  const webProg = doc.webProgress && typeof doc.webProgress === "object" ? (doc.webProgress as Record<string, unknown>) : null;
  const removed = webProg?.removed && typeof webProg.removed === "object"
    ? (webProg.removed as Record<string, unknown>)
    : null;
  let cwChanged = mergeContinueWatchingTombstonesForScope("", tombstonesFrom(removed?.owner));
  const removedBy = removed?.byProfile && typeof removed.byProfile === "object"
    ? (removed.byProfile as Record<string, unknown>)
    : null;
  if (removedBy) {
    for (const pid of Object.keys(removedBy)) {
      if (mergeContinueWatchingTombstonesForScope(pid, tombstonesFrom(removedBy[pid]))) cwChanged = true;
    }
  }

  // Continue Watching for the OWNER: derive from the synced library items' t/d progress, plus any explicit
  // vortx.continueWatching the app emits. cwEntriesFrom is shared with the per-profile hydration below.
  if (mergeContinueWatching([...cwEntriesFrom(vortx.continueWatching), ...cwEntriesFrom(libItems)])) cwChanged = true;

  // Profiles roster + per-profile (byProfile) library/CW. Without this the webapp only ever showed the
  // local "You" profile and never the user's real synced roster, and secondary profiles had empty
  // libraries. Ports the dashboard normalizeDoc essentials (roster + tombstones + profileEdits overlay)
  // and writes each profile's library/CW into its own scoped store keys. Read-only: never deletes.
  const rosterChanged = mergeSyncedProfiles(rosterFromDoc(vortx, doc));
  let byProfileChanged = false;
  const byProfile = vortx.byProfile && typeof vortx.byProfile === "object" ? (vortx.byProfile as Record<string, unknown>) : null;
  if (byProfile) {
    for (const pid of Object.keys(byProfile)) {
      const bp = byProfile[pid];
      if (!bp || typeof bp !== "object") continue;
      const rec = bp as Record<string, unknown>;
      // Apple writes the same timestamped canonical-key removals beside this profile's library. Fold them
      // before its live rows, in addition to webProgress.removed above, so either surface can dismiss safely.
      if (mergeContinueWatchingTombstonesForScope(pid, tombstonesFrom(rec.removed))) byProfileChanged = true;
      if (mergeLibraryForScope(pid, asObjArr(rec.library) as unknown as MetaItem[])) byProfileChanged = true;
      const cwSrc = Array.isArray(rec.continueWatching) ? rec.continueWatching : rec.library;
      if (mergeContinueWatchingForScope(pid, cwEntriesFrom(cwSrc))) byProfileChanged = true;
    }
  }

  // Web-written watch progress (the bilateral doc.webProgress field this client also WRITES). Shape:
  // { editedAt, owner: [entry], byProfile: { <overlayId>: [entry] } }, entry = {id,type,v,t,d,lastWatched}.
  // Mirrors the app's "owner top-level, overlays under byProfile" convention so the owner's web progress
  // merges into the base scope (not a phantom scope keyed by the owner's profile id). Merging it back means
  // what you watched on one browser shows on another even before the apps round-trip it into vortx.library.
  if (webProg) {
    if (Array.isArray(webProg.owner) && mergeContinueWatching(cwEntriesFrom(webProg.owner))) cwChanged = true;
    const wpBy = webProg.byProfile && typeof webProg.byProfile === "object" ? (webProg.byProfile as Record<string, unknown>) : null;
    if (wpBy) {
      for (const pid of Object.keys(wpBy)) {
        if (mergeContinueWatchingForScope(pid, cwEntriesFrom(wpBy[pid]))) byProfileChanged = true;
      }
    }
  }

  // Re-render: any add-on/library/CW change reloads the nav; a roster change repaints the profile switcher
  // and the scoped Home/Library/Continue-Watching for the active profile.
  if (typeof window !== "undefined") {
    if (addonsChanged || orderChanged || libraryChanged || cwChanged || rosterChanged || byProfileChanged) {
      window.dispatchEvent(new Event("vortx:addons-changed"));
    }
    if (rosterChanged) window.dispatchEvent(new CustomEvent("vortx:profile-changed", { detail: { fromSync: true } }));
  }
}

function tombstonesFrom(value: unknown): CWTombstone[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is CWTombstone => {
    if (!entry || typeof entry !== "object") return false;
    const record = entry as Record<string, unknown>;
    return Array.isArray(record.keys) && record.keys.every((key) => typeof key === "string") &&
      typeof record.removedAt === "number" && Number.isFinite(record.removedAt);
  });
}

/** After sign-in, pull the account's encrypted sync document and apply it locally, so the user's add-ons,
 *  library, and metadata keys come over from their other VortX devices. Fail-soft: a missing or
 *  undecryptable doc never blocks sign-in. */
export async function hydrateFromAccount(session: Session): Promise<void> {
  if (receiveState && sameSession(receiveState.session, session)) {
    await requestAccountPull(receiveState);
    return;
  }
  try {
    const doc = await getSyncDoc(session);
    if (sameSession(settingsSyncState.session, session)) applySyncDoc(doc);
  } catch {
    // network / decrypt failure: sign-in still succeeds with local state.
  }
}

interface ReceiveState {
  session: Session;
  receivedVersion?: number;
  appliedVersion?: number;
  projectionPending?: boolean;
  pull: Promise<void> | null;
  pullAgain: boolean;
  abort: AbortController | null;
  socket: WebSocket | null;
  pollTimer?: ReturnType<typeof setTimeout>;
  reconnectTimer?: ReturnType<typeof setTimeout>;
  reconnectAttempt: number;
}

let receiveStarted = false;
let receiveState: ReceiveState | null = null;

function syncIsActive(): boolean {
  return (typeof document === "undefined" || document.visibilityState !== "hidden") &&
    (typeof navigator === "undefined" || navigator.onLine !== false);
}

function isCurrentReceiver(state: ReceiveState): boolean {
  return receiveStarted && receiveState === state && sameSession(settingsSyncState.session, state.session);
}

function disposeReceiver(state: ReceiveState): void {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
  state.pollTimer = state.reconnectTimer = undefined;
  state.abort?.abort();
  if (state.socket) {
    state.socket.onopen = state.socket.onmessage = state.socket.onerror = state.socket.onclose = null;
    state.socket.close();
    state.socket = null;
  }
}

function bindReceiveSession(session: Session | null): void {
  if (!receiveStarted) return;
  if (session && receiveState && sameSession(receiveState.session, session)) return;
  if (receiveState) disposeReceiver(receiveState);
  receiveState = session ? {
    session, pull: null, pullAgain: false, abort: null, socket: null, reconnectAttempt: 0,
  } : null;
  if (receiveState) resumeReceiver(receiveState, true);
  notifyReceiveStatus();
}

function resumeReceiver(state: ReceiveState, flushWrites = false): void {
  if (!isCurrentReceiver(state) || !syncIsActive()) return;
  discardTransferredIntents(settingsSyncState);
  if (!settingsSyncState.writeQueued && restoreIntents(settingsSyncState, true)) {
    withSuppressedUp(() => updateSettings(isOwnerProfile(activeProfileId())
      ? settingsSyncState.ownerSettingsSnapshot : sharedSettingsPatch(settingsSyncState.ownerSettingsSnapshot)));
    persistIntents(settingsSyncState);
    scheduleIntentPush(settingsSyncState, 350);
  }
  for (const key of settingsSyncState.journalOwners) claimJournal(settingsSyncState, key);
  void requestAccountPull(state);
  connectReceiver(state);
  if (!state.pollTimer) state.pollTimer = setTimeout(() => {
    state.pollTimer = undefined;
    resumeReceiver(state);
  }, 3_000);
  if (flushWrites && hasPendingIntents(settingsSyncState)) {
    if (settingsSyncState.pushTimer) clearTimeout(settingsSyncState.pushTimer);
    queueSettingsPush(settingsSyncState);
  }
}

function connectReceiver(state: ReceiveState): void {
  if (state.socket || state.reconnectTimer || typeof WebSocket === "undefined") return;
  const retry = (): void => {
    if (!isCurrentReceiver(state) || !syncIsActive() || state.reconnectTimer) return;
    state.reconnectTimer = setTimeout(() => {
      state.reconnectTimer = undefined;
      if (isCurrentReceiver(state) && syncIsActive()) connectReceiver(state);
    }, Math.min(30_000, 1_000 * 2 ** Math.min(state.reconnectAttempt++, 5)));
  };
  try {
    const socket = connectSyncSocket(state.session);
    state.socket = socket;
    socket.onopen = () => {
      if (!isCurrentReceiver(state) || state.socket !== socket) return;
      state.reconnectAttempt = 0;
      void requestAccountPull(state); // closes the write-between-initial-GET-and-socket-open gap
    };
    socket.onmessage = (event) => {
      if (!isCurrentReceiver(state) || state.socket !== socket || !syncIsActive()) return;
      try {
        const message = JSON.parse(String(event.data));
        if (message.type === "updated" && Number.isSafeInteger(message.version) &&
          (state.receivedVersion === undefined || message.version > state.receivedVersion)) {
          void requestAccountPull(state);
        }
      } catch { /* Invalid notifications carry no account content and cannot trigger application. */ }
    };
    socket.onclose = () => {
      if (state.socket !== socket) return;
      state.socket = null;
      retry();
    };
    socket.onerror = () => {
      if (state.socket !== socket) return;
      state.socket = null;
      socket.onclose = null;
      socket.close();
      retry();
    };
  } catch { retry(); }
}

function requestAccountPull(state: ReceiveState): Promise<void> {
  if (!isCurrentReceiver(state) || !syncIsActive()) return Promise.resolve();
  if (state.pull) {
    state.pullAgain = true;
    return state.pull;
  }
  const run = async (): Promise<void> => {
    do {
      state.pullAgain = false;
      const abort = new AbortController();
      state.abort = abort;
      const deadline = setTimeout(() => abort.abort(), 15_000);
      try {
        const status = await fetchSync(state.session, { version: state.receivedVersion, signal: abort.signal });
        if (!isCurrentReceiver(state) || !syncIsActive() || abort.signal.aborted) return;
        if (status.unchanged || status.decryptFailed) continue;
        if (status.contents) {
          applySyncDoc(status.contents);
          // The legacy browser adapter cannot project native engine state yet. Retain its encrypted
          // fields on every write, and expose received separately from fully applied in the UI.
          state.projectionPending = ["nativeSync", "nativeHostPreferences", "nativeProviderCredentials"]
            .some((key) => status.contents![key] !== undefined);
          if (!state.projectionPending) state.appliedVersion = status.version;
        }
        state.receivedVersion = status.version;
        notifyReceiveStatus();
      } catch { /* Socket hints, fallback polling, and foreground/online catch-up retry automatically. */ }
      finally {
        clearTimeout(deadline);
        if (state.abort === abort) state.abort = null;
      }
    } while (state.pullAgain && isCurrentReceiver(state) && syncIsActive());
  };
  state.pull = run().finally(() => { state.pull = null; });
  return state.pull;
}

/** One automatic receiving lifecycle for this page. Account/token changes retire old callbacks. */
export function startAccountSync(): () => void {
  receiveStarted = true;
  bindReceiveSession(currentSession());
  const catchUp = (): void => {
    if (!receiveState) return;
    if (syncIsActive()) resumeReceiver(receiveState, true);
    else disposeReceiver(receiveState);
  };
  const pause = (): void => {
    if (receiveState) disposeReceiver(receiveState);
    releaseJournals(settingsSyncState);
  };
  window.addEventListener("online", catchUp);
  window.addEventListener("offline", catchUp);
  window.addEventListener("pageshow", catchUp);
  window.addEventListener("pagehide", pause);
  document.addEventListener("visibilitychange", catchUp);
  return () => {
    receiveStarted = false;
    if (receiveState) disposeReceiver(receiveState);
    receiveState = null;
    window.removeEventListener("online", catchUp);
    window.removeEventListener("offline", catchUp);
    window.removeEventListener("pageshow", catchUp);
    window.removeEventListener("pagehide", pause);
    document.removeEventListener("visibilitychange", catchUp);
  };
}

export function accountSyncStatus(): { receivedVersion?: number; appliedVersion?: number; projectionPending: boolean } {
  return { receivedVersion: receiveState?.receivedVersion, appliedVersion: receiveState?.appliedVersion,
    projectionPending: receiveState?.projectionPending ?? false };
}

function notifyReceiveStatus(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event("vortx:account-sync-changed"));
}

// --- Two-way sync: push web-authored changes back to the account --------------------------------
// The webapp writes ONLY web-owned sibling keys: doc.profileEdits (the main profile's settings),
// doc.addons (installed add-ons) and doc.webProgress. It NEVER writes doc.vortx.* (app-authoritative). Every write
// goes through mutateSyncDoc (optimistic concurrency: read version, merge, PUT version+1, retry on a
// stale-version rejection) so a concurrent app/device write is never clobbered.

const SETTINGS_PUSH_DELAY = 800;
const SETTINGS_RETRY_DELAY = 1_000;
const API_KEY_SETTING_KEYS: readonly (keyof Settings)[] = ["tmdbKey", "mdblistKey"];
// These are exactly the fields this module hydrates from and writes to the account document. Resetting
// them on an identity change prevents account A from remaining live while account B is still hydrating.
// Settings absent here are device-local and intentionally survive the transition.
const ACCOUNT_SCOPED_SETTING_DEFAULTS: Partial<Settings> = {
  accentID: "vortx",
  background: "warm",
  textScale: 1,
  audioLang: "",
  subtitleLang: "",
  subtitlesMode: "always",
  tmdbKey: "",
  mdblistKey: "",
  subtitleScale: 1,
  useAddonOrder: false,
  sourceOrder: ["debrid", "usenet", "torrent", "direct"],
  safetyFilter: "off",
  hideWords: "",
  requireWords: "",
  instantOnly: false,
  hideDeadTorrents: false,
  hdrOnly: false,
  hideAV1: false,
  maxQuality: 0,
  maxFileSizeGB: 0,
  subtitleFont: "modern",
  subtitleColor: "white",
  subtitleEdge: "outline",
};

interface SettingsSyncState {
  session: Session | null;
  suppressUpDepth: number;
  pushTimer: ReturnType<typeof setTimeout> | undefined;
  pendingKeys: Set<keyof Settings>;
  pendingSettings: Settings | null;
  writeQueued: boolean;
  profileAtLastChange: string;
  ownerSettingsSnapshot: Settings;
  addonIntents: Record<string, AddonIntent>;
  progressIntents: Record<string, ProgressIntent>;
  revision: number;
  retryAttempt: number;
  journalSources: Map<string, string>;
  journalOwners: Set<string>;
}

interface AddonIntent { url: string; added: boolean; at: number; revision: number;
  descriptor?: { transportUrl: string; name: string; manifest: unknown } }
interface ProgressIntent { entries: WebProgressEntry[]; tombstones: CWTombstone[]; revision: number }

let journalClientId: string | undefined;
function randomSyncId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
const journalPageId = randomSyncId();

function journalOwner(key: string): { id?: string; until?: number } {
  try { return JSON.parse(localStorage.getItem(key + ".owner") ?? "{}"); } catch { return {}; }
}

function claimJournal(state: SettingsSyncState, key: string): boolean {
  const owner = journalOwner(key);
  if (owner.id !== journalPageId && Number(owner.until) > Date.now()) return false;
  try {
    localStorage.setItem(key + ".owner", JSON.stringify({ id: journalPageId, until: Date.now() + 30_000 }));
    state.journalOwners.add(key);
  } catch { /* Storage unavailable: the current tab still retains its in-memory intent. */ }
  return true;
}

function ownsJournals(state: SettingsSyncState): boolean {
  return [...state.journalOwners].every((key) => {
    const owner = journalOwner(key);
    try { return owner.id === journalPageId || (owner.id === undefined && localStorage.getItem(key) !== null); }
    catch { return true; /* Storage unavailable: keep this page's in-memory queue. */ }
  });
}

function releaseJournals(state: SettingsSyncState): void {
  for (const key of state.journalOwners) {
    if (journalOwner(key).id !== journalPageId) continue;
    // Keep the ownership receipt after ACK. A suspended older page must see that its journal
    // transferred, even after the recovering page removes the completed journal itself.
    try { localStorage.setItem(key + ".owner", JSON.stringify({ id: journalPageId, until: 0 })); }
    catch { /* lease will expire */ }
  }
}

function discardTransferredIntents(state: SettingsSyncState): void {
  if (ownsJournals(state)) return;
  // Another page recovered this inactive page's exact durable journal. Its writer now owns those
  // intents; this page must not replay an older copy when it resumes or accepts a fresh edit.
  state.pendingKeys.clear(); state.pendingSettings = null;
  state.addonIntents = Object.create(null); state.progressIntents = Object.create(null);
  state.journalSources.clear(); state.journalOwners.clear();
}

function journalKey(session: Session): string {
  if (!journalClientId) {
    try { journalClientId = sessionStorage.getItem("vortx.web.sync-client.v1") ?? undefined; } catch { /* optional */ }
    journalClientId ??= randomSyncId();
    try { sessionStorage.setItem("vortx.web.sync-client.v1", journalClientId); } catch { /* in-memory queue remains */ }
  }
  const prefix = "vortx.web.sync-intents.v1." + encodeURIComponent(session.account.id) + ".";
  let key = prefix + journalClientId;
  const owner = journalOwner(key);
  if (owner.id !== journalPageId && Number(owner.until) > Date.now()) {
    journalClientId = randomSyncId();
    try { sessionStorage.setItem("vortx.web.sync-client.v1", journalClientId); } catch { /* optional */ }
    key = prefix + journalClientId;
  }
  return key;
}

// Only explicit account edits go in this journal. It carries no token, data key, or socket URL.
// A synchronous write happens before scheduling network work, including before pagehide/reload.
function persistIntents(state: SettingsSyncState): void {
  if (!state.session) return;
  const settings: Partial<Settings> = {};
  for (const key of state.pendingKeys) {
    if (state.pendingSettings) Object.assign(settings, { [key]: state.pendingSettings[key] });
  }
  try {
    if (!state.pendingKeys.size && !Object.keys(state.addonIntents).length && !Object.keys(state.progressIntents).length) {
      localStorage.removeItem(journalKey(state.session));
      for (const [key, raw] of state.journalSources) {
        if (localStorage.getItem(key) === raw) localStorage.removeItem(key);
      }
      state.journalSources.clear();
      releaseJournals(state);
      state.journalOwners.clear();
    } else {
      const key = journalKey(state.session);
      claimJournal(state, key);
      localStorage.setItem(key, JSON.stringify({
        accountId: state.session.account.id, editedAt: Date.now(),
        settings, addons: state.addonIntents, progress: state.progressIntents, revision: state.revision,
      }));
    }
  } catch { /* Storage unavailable: retain the live queue and retry while the tab remains open. */ }
}

function restoreIntents(state: SettingsSyncState, onlyOrphans = false): boolean {
  if (!state.session) return false;
  try {
    const keys = new Set([journalKey(state.session)]);
    const prefix = "vortx.web.sync-intents.v1.";
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith(prefix)) keys.add(key);
    }
    const journals = [...keys].flatMap((key) => {
      try {
        const raw = localStorage.getItem(key);
        if (!raw) return [];
        const saved = JSON.parse(raw);
        if (onlyOrphans && journalOwner(key).id === journalPageId) return [];
        return saved.accountId === state.session!.account.id && claimJournal(state, key) ? [{ key, raw, saved }] : [];
      } catch { return []; }
    }).sort((a, b) => (Number(a.saved.editedAt) || 0) - (Number(b.saved.editedAt) || 0));
    const snapshot = copySettings(state.ownerSettingsSnapshot);
    for (const { key: sourceKey, raw, saved } of journals) {
      state.journalSources.set(sourceKey, raw);
      state.revision = Math.max(state.revision, Number(saved.revision) || 0);
      if (saved.settings && typeof saved.settings === "object") {
        for (const key of Object.keys(saved.settings) as (keyof Settings)[]) {
          const value = saved.settings[key];
          if ((onlyOrphans && state.pendingKeys.has(key)) || !(key in snapshot) || (key === "sourceOrder"
            ? !Array.isArray(value) || !value.every((v: unknown) => typeof v === "string")
            : typeof value !== typeof snapshot[key])) continue;
          Object.assign(snapshot, { [key]: value });
          state.pendingKeys.add(key);
        }
      }
      if (state.pendingKeys.size) {
        state.pendingSettings = snapshot;
        state.ownerSettingsSnapshot = snapshot;
      }
      for (const value of Object.values(saved.addons ?? {})) {
        const intent = value as AddonIntent;
        if (typeof intent?.url !== "string" || typeof intent.added !== "boolean" || !Number.isFinite(intent.at)) continue;
        const key = addonMembershipKey(intent.url);
        if (!state.addonIntents[key] || state.addonIntents[key].at <= intent.at) state.addonIntents[key] = intent;
      }
      for (const [scope, value] of Object.entries(saved.progress ?? {})) {
        const intent = value as ProgressIntent;
        if (!intent || !Array.isArray(intent.entries) || !Array.isArray(intent.tombstones)) continue;
        state.progressIntents[scope] = {
          entries: mergeWebProgress(state.progressIntents[scope]?.entries, intent.entries),
          tombstones: mergeWebProgressTombstones(state.progressIntents[scope]?.tombstones, tombstonesFrom(intent.tombstones)),
          revision: ++state.revision,
        };
      }
    }
    return journals.length > 0;
  } catch { return false; /* Invalid journals cannot become an account write. */ }
}

let settingsSyncState: SettingsSyncState;
// Every settings mutation, including one queued under a refreshed session, runs after the previous one.
// Jobs still verify their captured state before starting, so a queued write from a departed account drops.
let settingsWriteTail: Promise<void> = Promise.resolve();

function sameSession(a: Session | null, b: Session | null): boolean {
  if (a === b) return true;
  if (!sameAccountScope(a, b) || !a || !b || a.token !== b.token) {
    return false;
  }
  return true;
}

function sameAccountScope(a: Session | null, b: Session | null): boolean {
  if (a === b) return true;
  if (!a || !b || a.account.id !== b.account.id || a.dataKey.length !== b.dataKey.length) return false;
  return a.dataKey.every((byte, index) => byte === b.dataKey[index]);
}

function copySettings(settings: Settings): Settings {
  return { ...settings, sourceOrder: [...settings.sourceOrder] };
}

// profiles.ts deliberately owns only these six fields per profile. Every other Settings field is shared
// account state even while an overlay is active.
const PROFILE_SCOPED_SETTING_KEYS: readonly (keyof Settings)[] = [
  "accentID",
  "background",
  "textScale",
  "audioLang",
  "subtitleLang",
  "subtitlesMode",
];

function sharedSettingsPatch(settings: Partial<Settings>): Partial<Settings> {
  const shared = { ...settings };
  for (const key of PROFILE_SCOPED_SETTING_KEYS) delete shared[key];
  if (shared.sourceOrder) shared.sourceOrder = [...shared.sourceOrder];
  return shared;
}

function changedSettingKeys(a: Settings, b: Settings): (keyof Settings)[] {
  return (Object.keys(b) as (keyof Settings)[]).filter((key) => {
    if (key === "sourceOrder") return JSON.stringify(a.sourceOrder) !== JSON.stringify(b.sourceOrder);
    return a[key] !== b[key];
  });
}

function initialOwnerSettings(): Settings {
  const live = getSettings();
  const ownerLook = localProfiles()[0]?.look;
  return copySettings(ownerLook ? { ...live, ...ownerLook } : live);
}

function createSettingsSyncState(session: Session | null, ownerSnapshot?: Settings): SettingsSyncState {
  const state: SettingsSyncState = {
    session,
    suppressUpDepth: 0,
    pushTimer: undefined,
    pendingKeys: new Set(),
    pendingSettings: null,
    writeQueued: false,
    profileAtLastChange: activeProfileId(),
    ownerSettingsSnapshot: copySettings(ownerSnapshot ?? initialOwnerSettings()),
    addonIntents: Object.create(null), progressIntents: Object.create(null), revision: 0, retryAttempt: 0,
    journalSources: new Map(),
    journalOwners: new Set(),
  };
  restoreIntents(state);
  return state;
}

settingsSyncState = createSettingsSyncState(null);

function bindSettingsSyncState(session: Session | null): void {
  if (sameAccountScope(settingsSyncState.session, session)) {
    settingsSyncState.session = session;
    return;
  }
  if (settingsSyncState.pushTimer) clearTimeout(settingsSyncState.pushTimer);
  releaseJournals(settingsSyncState);
  let resetSnapshot: Settings | undefined;
  withSuppressedUp(() => {
    resetSnapshot = updateSettings({
      ...ACCOUNT_SCOPED_SETTING_DEFAULTS,
      sourceOrder: [...(ACCOUNT_SCOPED_SETTING_DEFAULTS.sourceOrder ?? [])],
    });
  });
  settingsSyncState = createSettingsSyncState(session, resetSnapshot);
  if (session) {
    withSuppressedUp(() => updateSettings(settingsSyncState.ownerSettingsSnapshot));
    scheduleIntentPush(settingsSyncState, 350);
  }
}

// Guard so settings applied by read-down hydration do not immediately echo back up as a web edit.
function withSuppressedUp(fn: () => void): void {
  const state = settingsSyncState;
  state.suppressUpDepth += 1;
  try {
    fn();
  } finally {
    state.suppressUpDepth -= 1;
  }
}

function rememberOwnerSettings(settings: Settings): void {
  settingsSyncState.ownerSettingsSnapshot = copySettings(settings);
}

function mergeOwnerSettings(patch: Partial<Settings>): Settings {
  const ownerSettingsSnapshot = settingsSyncState.ownerSettingsSnapshot;
  const next = {
    ...ownerSettingsSnapshot,
    ...patch,
    sourceOrder: patch.sourceOrder ? [...patch.sourceOrder] : [...ownerSettingsSnapshot.sourceOrder],
  };
  rememberOwnerSettings(next);
  return next;
}

function restoreOwnerSettings(): void {
  withSuppressedUp(() => updateSettings(copySettings(settingsSyncState.ownerSettingsSnapshot)));
}

onProfilesChange(() => {
  const state = settingsSyncState;
  const profileId = activeProfileId();
  if (profileId === state.profileAtLastChange) return;
  state.profileAtLastChange = profileId;
  if (isOwnerProfile(profileId)) restoreOwnerSettings();
});

/** Build the roster carried by a settings write from the latest effective account state. The app mirror
 *  supplies the acknowledged base. A newer web roster overlays it row by row and retains every pending
 *  web-only field, row, and tombstone. The caller then changes only the main row's settings. */
function rosterForSettingsWrite(
  doc: Record<string, unknown>,
  mainId: string,
  mergedSettings: Record<string, unknown>,
): Record<string, unknown>[] {
  const vortx = (doc.vortx && typeof doc.vortx === "object" ? doc.vortx : {}) as Record<string, unknown>;
  const edits =
    doc.profileEdits && typeof doc.profileEdits === "object"
      ? (doc.profileEdits as Record<string, unknown>)
      : {};
  const order: string[] = [];
  const byId = new Map<string, Record<string, unknown>>();
  const put = (row: Record<string, unknown>): void => {
    if (row.id == null) return;
    const id = String(row.id);
    if (!byId.has(id)) order.push(id);
    byId.set(id, { ...(byId.get(id) ?? {}), ...row, id });
  };

  for (const profile of asObjArr(vortx.profiles)) {
    if (profile.id == null) continue;
    put({ id: String(profile.id), name: String(profile.name ?? "Profile") });
  }
  const editsAreEffective = (Number(edits.editedAt) || 0) > (Number(vortx.updatedAt) || 0);
  if (editsAreEffective) {
    for (const row of asObjArr(edits.roster)) put(row);
  }

  const main = byId.get(mainId) ?? { id: mainId, name: "Profile" };
  byId.set(mainId, { ...main, settings: mergedSettings });
  if (!order.includes(mainId)) order.unshift(mainId);
  return order.map((id) => byId.get(id)!);
}

/** Push the changed webapp settings up to the MAIN profile via doc.profileEdits (dashboard-compatible shape).
 *  Delta-merges the main settings onto the latest effective app plus web roster, preserving unrelated
 *  profile edits and settings. Profile settings no-op when there is no synced main profile, while
 *  API-key deltas remain independent. */
async function pushSettings(
  session: Session,
  s: Settings,
  changedKeys: readonly (keyof Settings)[],
  isCurrent: () => boolean = () => true,
): Promise<void> {
  await mutateSyncDoc(session, (doc) => {
    const changed = new Set(changedKeys);
    const mainId = mainProfileId(doc);
    if (!mainId && changedKeys.some((key) => key in ACCOUNT_SCOPED_SETTING_DEFAULTS && !API_KEY_SETTING_KEYS.includes(key))) {
      throw new Error("Account profile projection is not available; settings intent remains queued.");
    }
    if (mainId) {
      const edits = (doc.profileEdits && typeof doc.profileEdits === "object" ? doc.profileEdits : {}) as Record<string, unknown>;
      const base = effectiveMainSettings(doc); // freshest known main settings (app mirror + newer overlay)
      const merged = mergeWebappSettingsPatchIntoProfile(base, s, changedKeys);
      if (JSON.stringify(merged) !== JSON.stringify(base)) {
        doc.profileEdits = {
          ...edits,
          editedAt: Date.now(),
          roster: rosterForSettingsWrite(doc, mainId, merged),
          libraryAdds: (edits as Record<string, unknown>).libraryAdds ?? {},
        };
      }
    }

    if (changed.has("tmdbKey") || changed.has("mdblistKey")) {
      const apiKeys =
        doc.apiKeys && typeof doc.apiKeys === "object"
          ? { ...(doc.apiKeys as Record<string, unknown>) }
          : {};
      if (changed.has("tmdbKey")) {
        if (s.tmdbKey) apiKeys.tmdb = s.tmdbKey;
        else delete apiKeys.tmdb;
      }
      if (changed.has("mdblistKey")) {
        if (s.mdblistKey) apiKeys.mdblist = s.mdblistKey;
        else delete apiKeys.mdblist;
      }
      doc.apiKeys = apiKeys;
    }
  }, isCurrent);
}

/** Tombstone-identity normalization: trim + lowercase, byte-for-byte matching the app's
 *  AddonTombstones.normalize, so a URL the web writes into doc.webAddonRemovals matches what the app
 *  compares its installed add-ons against. */
function normalizeAddonUrl(u: string): string {
  return u.trim().toLowerCase();
}

// Descriptor membership preserves case-sensitive path/query/credential bytes. Legacy tombstones
// continue to use their established normalization until every client supports exact membership.
function addonMembershipKey(u: string): string {
  const value = u.trim();
  const match = /^([a-z][a-z0-9+.-]*):\/\/([^/?#]+)([\s\S]*)$/i.exec(value);
  if (!match) return value;
  const authority = match[2];
  const split = authority.lastIndexOf("@");
  const credentials = split >= 0 ? authority.slice(0, split + 1) : "";
  const host = authority.slice(split + 1).toLowerCase();
  return `${match[1].toLowerCase()}://${credentials}${host}${match[3]}`;
}

/** Push the webapp's installed add-ons up to the account (the doc.addons web sibling), so add-ons added
 *  on the web reach the user's other devices AND the native apps. Cinemeta is excluded (a universal
 *  built-in). Serialized + coalesced by the caller. Fail-soft. */
async function pushAddons(
  session: Session,
  intents: AddonIntent[],
  isCurrent: () => boolean,
): Promise<void> {
    // Pre-resolve each URL's FULL manifest (async, outside the mutate): the app installs from doc.addons
    // network-free and DROPS any entry without a manifest, so a URL-only entry would never reach the apps.
    const resolved = new Map<string, { transportUrl: string; name: string; manifest: unknown }>();
    for (const intent of intents) if (intent.descriptor) resolved.set(intent.url, intent.descriptor);
    await Promise.all(
      intents
        .filter((intent) => intent.added && !intent.descriptor && intent.url !== CINEMETA_URL)
        .map(async ({ url: u }) => {
          try {
            const a = await loadAddon(u);
            resolved.set(u, { transportUrl: a.transportUrl, name: a.manifest.name, manifest: a.manifest });
          } catch {
            /* leave unresolved; handled inside the mutate (preserve prior descriptor or skip) */
          }
        }),
    );
    await mutateSyncDoc(session, (doc) => {
      // doc.addons is the WEB-ONLY sibling channel. Exclude app-owned URLs (doc.vortx.addons): the webapp's
      // installedUrls includes app add-ons it hydrated in, and copying them here makes the dashboard's
      // diff-based setAddons false-tombstone an untouched app add-on. (audit: web-pollutes-doc.addons)
      const vortx = (doc.vortx && typeof doc.vortx === "object" ? doc.vortx : {}) as Record<string, unknown>;
      const appUrls = new Set(
        (Array.isArray(vortx.addons) ? vortx.addons : []).map((a) => addonUrl(a)).filter(Boolean) as string[],
      );
      const priorByUrl = new Map<string, { transportUrl: string; name?: string; manifest?: unknown }>();
      for (const a of Array.isArray(doc.addons) ? doc.addons : []) {
        const u = addonUrl(a);
        if (u) priorByUrl.set(u, a as { transportUrl: string; name?: string; manifest?: unknown });
      }
      // Change only explicit membership intent. A device's inherited store must not overwrite remote
      // additions or copy account A's add-ons into a newly signed-in account B.
      const descriptors: unknown[] = [...(Array.isArray(doc.addons) ? doc.addons : [])];
      for (const intent of intents) {
        const u = intent.url;
        if (u === CINEMETA_URL) continue;
        for (let i = descriptors.length - 1; i >= 0; i--) {
          const priorUrl = addonUrl(descriptors[i]);
          if (priorUrl && addonMembershipKey(priorUrl) === addonMembershipKey(u)) descriptors.splice(i, 1);
        }
        if (!intent.added || appUrls.has(u)) continue;
        const prior = priorByUrl.get(u);
        const descriptor = resolved.get(u) ?? (prior?.manifest ? prior : undefined);
        if (!descriptor) throw new Error("Add-on manifest unavailable; retry pending install.");
        descriptors.push({ ...(prior && typeof prior === "object" ? prior : {}), ...descriptor });
      }
      doc.addons = descriptors;

      // Removal tombstones, from the EXPLICIT add/remove hints (never inferred by diffing, which would
      // false-tombstone app add-ons). doc.removedAddons {url: epochMs} is the web-internal record (carries
      // timestamps so a re-add can clear it). doc.webAddonRemovals is the flat string[] the APP reads and
      // folds into its uninstall set (VortXSyncManager: "web agent owns this write; we only READ it").
      for (const intent of intents) {
        applyAddonIntents(doc, intent.added ? { added: [intent.url] } : { removed: [intent.url] }, intent.at);
      }
    }, isCurrent);
}

// Capture exact add/remove intent and the already validated manifest before scheduling any upload.
registerAddonsSyncPusher((hint) => {
  if (!currentSession() || settingsSyncState.suppressUpDepth > 0) return;
  const state = settingsSyncState;
  discardTransferredIntents(state);
  const url = hint?.added ?? hint?.removed;
  if (!url) return;
  const key = addonMembershipKey(url);
  state.addonIntents[key] = { url, added: !!hint?.added,
    at: Math.max(Date.now(), (state.addonIntents[key]?.at ?? 0) + 1), revision: ++state.revision,
    descriptor: hint?.descriptor ? { transportUrl: hint.descriptor.transportUrl,
      name: hint.descriptor.manifest.name, manifest: hint.descriptor.manifest } : undefined };
  persistIntents(state);
  scheduleIntentPush(state, 350);
}, () => {
  const session = currentSession();
  const state = settingsSyncState;
  return () => state === settingsSyncState && sameAccountScope(state.session, session);
});

// Push the ACTIVE profile's web watch-progress up to the bilateral doc.webProgress field. Owner progress
// goes top-level (doc.webProgress.owner) to match the app's convention; overlay progress under
// .byProfile[profileId]. The app merges this into watch history (LWW by lastWatched), NEVER into
// libraryItem. Fail-soft. (Read-down lives in applySyncDoc.)
async function pushWebProgress(session: Session,
  scope = isOwnerProfile(activeProfileId()) ? "" : activeProfileId(),
  intent: ProgressIntent = { entries: webProgressEntries(), tombstones: webProgressTombstones(), revision: 0 },
  isCurrent: () => boolean = () => true,
): Promise<void> {
    const { entries, tombstones } = intent;
    const owner = scope === "";
    const pid = scope;
    await mutateSyncDoc(session, (doc) => {
      const wp: Record<string, unknown> =
        doc.webProgress && typeof doc.webProgress === "object" ? (doc.webProgress as Record<string, unknown>) : {};
      const prior = owner
        ? wp.owner
        : wp.byProfile && typeof wp.byProfile === "object"
          ? (wp.byProfile as Record<string, unknown>)[pid]
          : undefined;
      const merged = mergeWebProgress(prior, entries);
      if (owner) {
        wp.owner = merged;
      } else {
        const by: Record<string, unknown> =
          wp.byProfile && typeof wp.byProfile === "object" ? (wp.byProfile as Record<string, unknown>) : {};
        by[pid] = merged;
        wp.byProfile = by;
      }
      const removed: Record<string, unknown> =
        wp.removed && typeof wp.removed === "object" ? (wp.removed as Record<string, unknown>) : {};
      if (owner) {
        removed.owner = mergeWebProgressTombstones(removed.owner, tombstones);
      } else {
        const by: Record<string, unknown> =
          removed.byProfile && typeof removed.byProfile === "object"
            ? (removed.byProfile as Record<string, unknown>)
            : {};
        by[pid] = mergeWebProgressTombstones(by[pid], tombstones);
        removed.byProfile = by;
      }
      wp.removed = removed;
      wp.editedAt = Date.now();
      doc.webProgress = wp;
    }, isCurrent);
}

function mergeWebProgressTombstones(prior: unknown, mine: CWTombstone[]): CWTombstone[] {
  return mergeCWTombstones(tombstonesFrom(prior), mine);
}

export const __continueWatchingSyncTestHooks = { pushWebProgress, mergeWebProgress };

// Union the doc's existing web-progress entries with this device's, keyed by id+played-id, keeping the
// fresher lastWatched. Without this, a full-array overwrite would let two browsers clobber each other's
// progress (LWW on the array instead of per title). Pure - returns a new array, never mutates inputs.
function mergeWebProgress(prior: unknown, mine: ReturnType<typeof webProgressEntries>): ReturnType<typeof webProgressEntries> {
  const byKey = new Map<string, (typeof mine)[number]>();
  const take = (e: unknown): void => {
    if (!e || typeof e !== "object") return;
    const r = e as Record<string, unknown>;
    if (typeof r.id !== "string" || typeof r.v !== "string") return;
    const key = `${r.id}|${r.v}`;
    const lastWatched = typeof r.lastWatched === "number" ? r.lastWatched : 0;
    const existing = byKey.get(key);
    if (!existing || lastWatched >= existing.lastWatched) {
      byKey.set(key, {
        ...existing,
        ...r,
        id: r.id,
        type: typeof r.type === "string" ? r.type : "",
        v: r.v,
        t: typeof r.t === "number" ? r.t : 0,
        d: typeof r.d === "number" ? r.d : 0,
        lastWatched,
        name: typeof r.name === "string" ? r.name : undefined,
        poster: typeof r.poster === "string" ? r.poster : undefined,
      });
    }
  };
  if (Array.isArray(prior)) prior.forEach(take);
  mine.forEach(take);
  return Array.from(byKey.values())
    .sort((a, b) => b.lastWatched - a.lastWatched)
    .slice(0, 100); // cap the synced array; locally we still keep the 40 most-recent per the CW store
}

// recordProgress fires ~every 5s during playback. Journal the edited row immediately and send within
// one second; a profile switch, failed PUT or closed page cannot lose or retarget that snapshot.
const WEBPROGRESS_PUSH_DELAY = 1_000;
registerCwSyncPusher((hint) => {
  if (settingsSyncState.suppressUpDepth > 0) return; // hydration merges CW too; don't echo synced progress back up
  if (!currentSession()) return; // signed out: progress stays local
  if (!hint) return; // automatic writes only carry the explicit edit, never an inherited account store
  const state = settingsSyncState;
  const scope = isOwnerProfile(activeProfileId()) ? "" : activeProfileId();
  discardTransferredIntents(state);
  const prior = state.progressIntents[scope];
  const entries = hint.entry ? [{ id: hint.entry.id, type: hint.entry.type, v: hint.entry.resumeId,
    t: hint.entry.position, d: hint.entry.duration, lastWatched: hint.entry.updatedAt,
    name: hint.entry.name, poster: hint.entry.poster }] : [];
  state.progressIntents[scope] = {
    entries: mergeWebProgress(prior?.entries, entries),
    tombstones: mergeWebProgressTombstones(prior?.tombstones, hint.removal ? [hint.removal] : []),
    revision: ++state.revision,
  };
  persistIntents(state);
  scheduleIntentPush(state, WEBPROGRESS_PUSH_DELAY);
});
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    if (settingsSyncState.pushTimer) clearTimeout(settingsSyncState.pushTimer);
    queueSettingsPush(settingsSyncState); // durable intent survives even when pagehide fetch is canceled
  });
}

function syncableSettingsKeys(
  changedKeys: readonly (keyof Settings)[],
): (keyof Settings)[] {
  // Explicit edits are durable even while the first account GET is pending. The writer separately
  // waits for a usable main-profile projection; hydration never manufactures a dirty key.
  return changedKeys.filter((key) => key in ACCOUNT_SCOPED_SETTING_DEFAULTS);
}

function queueSettingsPush(state: SettingsSyncState): void {
  state.pushTimer = undefined;
  discardTransferredIntents(state);
  if (state !== settingsSyncState || !state.session || !hasPendingIntents(state) || state.writeQueued) return;
  state.writeQueued = true;

  const write = async (): Promise<void> => {
    if (state !== settingsSyncState || !state.session) {
      state.writeQueued = false;
      return;
    }
    const session = state.session;
    const isCurrent = () => state === settingsSyncState && sameSession(state.session, session) && ownsJournals(state);
    let failed = false;
    const attempt = async (job: () => Promise<void>): Promise<void> => {
      try { if (isCurrent()) await job(); } catch { failed = true; }
    };
    try {
      if (state.pendingSettings && state.pendingKeys.size) {
        await attempt(async () => {
          if (!state.pendingSettings) return;
          const snapshot = copySettings(state.pendingSettings);
          const changedKeys = [...state.pendingKeys];
          await pushSettings(session, snapshot, changedKeys, isCurrent);
          if (!isCurrent()) throw new Error("Account session changed.");
          for (const key of changedKeys) {
            if (JSON.stringify(state.pendingSettings?.[key]) === JSON.stringify(snapshot[key])) state.pendingKeys.delete(key);
          }
          if (!state.pendingKeys.size) state.pendingSettings = null;
          persistIntents(state);
        });
      }
      const addons = Object.values(state.addonIntents);
      if (addons.length) {
        await attempt(async () => {
          await pushAddons(session, addons, isCurrent);
          if (!isCurrent()) throw new Error("Account session changed.");
          for (const intent of addons) {
            const key = addonMembershipKey(intent.url);
            if (state.addonIntents[key]?.revision === intent.revision) delete state.addonIntents[key];
          }
          persistIntents(state);
        });
      }
      for (const [scope, intent] of Object.entries(state.progressIntents)) {
        await attempt(async () => {
          await pushWebProgress(session, scope, intent, isCurrent);
          if (!isCurrent()) throw new Error("Account session changed.");
          if (state.progressIntents[scope]?.revision === intent.revision) delete state.progressIntents[scope];
          persistIntents(state);
        });
      }
      if (!failed) state.retryAttempt = 0;
    } catch {
      failed = true; // Unacknowledged intent never leaves the journal, including across reload/sign-out.
    } finally {
      state.writeQueued = false;
      if (state === settingsSyncState && state.session && hasPendingIntents(state)) {
        scheduleIntentPush(state, failed
          ? Math.min(30_000, SETTINGS_RETRY_DELAY * 2 ** Math.min(state.retryAttempt++, 5))
          : 350);
      }
    }
  };
  settingsWriteTail = settingsWriteTail.then(write, write);
}

function hasPendingIntents(state: SettingsSyncState): boolean {
  return state.pendingKeys.size > 0 || Object.keys(state.addonIntents).length > 0 || Object.keys(state.progressIntents).length > 0;
}

function scheduleIntentPush(state: SettingsSyncState, delay: number): void {
  if (state !== settingsSyncState || !state.session || !hasPendingIntents(state) || state.pushTimer) return;
  state.pushTimer = setTimeout(() => queueSettingsPush(state), delay);
}

function scheduleSettingsPush(
  state: SettingsSyncState,
  settings: Settings,
  changedKeys: readonly (keyof Settings)[],
): void {
  for (const key of changedKeys) state.pendingKeys.add(key);
  state.pendingSettings = copySettings(settings);
  persistIntents(state);
  if (state.pushTimer) clearTimeout(state.pushTimer);
  state.pushTimer = setTimeout(() => queueSettingsPush(state), SETTINGS_PUSH_DELAY);
}

onSettingsChange((next) => {
  const state = settingsSyncState;
  discardTransferredIntents(state);
  const profileId = activeProfileId();
  const switchedProfile = profileId !== state.profileAtLastChange;
  state.profileAtLastChange = profileId;
  if (state.suppressUpDepth > 0) {
    if (isOwnerProfile(profileId)) rememberOwnerSettings(next);
    return; // hydration applied this, not the user; don't echo it back up
  }
  if (switchedProfile) {
    if (isOwnerProfile(profileId)) restoreOwnerSettings();
    return; // applying another profile's local snapshot is not a settings edit
  }
  const previousOwner = state.ownerSettingsSnapshot;
  const ownerNext = isOwnerProfile(profileId)
    ? copySettings(next)
    : mergeOwnerSettings(sharedSettingsPatch(next));
  const changedKeys = changedSettingKeys(previousOwner, ownerNext);
  if (!changedKeys.length) {
    return; // the overlay changed only one of its six profile-scoped fields
  }
  rememberOwnerSettings(ownerNext);
  const syncableKeys = syncableSettingsKeys(changedKeys);
  if (!syncableKeys.length) return;
  const session = currentSession();
  if (!session || state !== settingsSyncState || !sameSession(state.session, session)) return;
  scheduleSettingsPush(state, ownerNext, syncableKeys);
});

/** Sign out: clear storage, reset the cache, and notify subscribers so the nav drops back to signed-out. */
export function signOut(): void {
  clearSession();
  setSession(null);
}

/** Subscribe to sign-in / sign-out. Fires once immediately with the current session, then on every
 *  change. Returns an unsubscribe function. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  listener(currentSession());
  return () => {
    listeners.delete(listener);
  };
}

function notify(): void {
  const value = cached ?? null;
  for (const listener of listeners) listener(value);
}
