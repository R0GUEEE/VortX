package com.vortx.android.sync

import android.content.Context
import com.vortx.android.BuildConfig
import com.vortx.android.profile.ProfileStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.json.JSONObject
import org.json.JSONArray
import com.vortx.android.profile.UserProfile
import com.vortx.android.profile.PlaybackPrefs
import com.vortx.android.profile.WatchEntry
import com.vortx.android.backup.SettingsBackup
import org.junit.Assert.*
import org.junit.Test

/** Runs only in the explicit legacy configuration, with the actual manager/crypto/roster merge. */
@OptIn(ExperimentalCoroutinesApi::class)
class VortXSyncManagerLegacyBackupRevisionTest {
    private class Fixture {
        val context = RevisionContext()
        val manager = VortXSyncManager(context)
        val key = ByteArray(32) { 42 }
        val account = VortXSyncManager.Account("legacy-revision-fixture", "fixture@example.invalid", "Fixture", false)
        var revision: Long? = 40L
        var cloud = JSONObject().put("baseline", "keep")
        var beforePut: ((Int) -> Unit)? = null
        var rawGet: JSONObject? = null
        var failGet = false
        var loseAcceptedResponse = false
        var putFailure: Pair<Int, JSONObject?>? = null
        val attempts = mutableListOf<Long>()
        val bases = mutableListOf<Long?>()
        val store: ProfileStore
        init {
            check(!BuildConfig.NATIVE_ENGINE_ENABLED)
            store = ProfileStore::class.java.getDeclaredConstructor(Context::class.java)
                .apply { isAccessible = true }.newInstance(context)
            // Reflection bypasses production get()'s bootstrap. Seed the same canonical owner
            // without global singleton/device initialization or asynchronous edit callbacks.
            store.onRosterPush = {}
            store.add(UserProfile(id = UserProfile.OWNER_ID, name = "Main", avatar = "🍿", isOwner = true))
            manager.attachSyncSeams(store)
            manager.installSyncTestSeam(VortXSyncManager.Session("fixture-only", account, key), 0, { method, _, body, _ ->
                if (method == "GET") {
                    val version = revision
                    when {
                        failGet -> 500 to null
                        rawGet != null -> 200 to rawGet
                        version == null -> 404 to null
                        else -> 200 to JSONObject().put("version", version).put("document",
                            VortXCrypto.sealDocument(key, cloud.toString().toByteArray(), account.id, version, true))
                    }
                } else {
                    val request = requireNotNull(body); val version = request.getLong("version")
                    check(request.has("baseRevision"))
                    val base = if (request.isNull("baseRevision")) null else request.getLong("baseRevision")
                    bases += base
                    attempts += version
                    beforePut?.invoke(attempts.size)
                    if (putFailure != null) requireNotNull(putFailure)
                    else if (base == revision && version == BackupRevisionPolicy.next(base)) {
                        cloud = JSONObject(String(requireNotNull(VortXCrypto.openDocument(key, request.getString("document"), account.id, version))))
                        revision = version
                        if (loseAcceptedResponse) { loseAcceptedResponse = false; 0 to null }
                        else 200 to JSONObject().put("accepted", true)
                    } else 200 to JSONObject().put("accepted", false).put("version", revision ?: JSONObject.NULL)
                }
            })
        }
    }

    @Test fun `legacy merged upload also rejects stale peer then preserves fresh winner fields`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.beforePut = { if (it == 1) { fixture.cloud.put("peerA", "must survive"); fixture.revision = 41 } }
            assertTrue(fixture.manager.syncUp())
            assertEquals(listOf(41L, 42L), fixture.attempts)
            assertEquals(listOf(40L, 41L), fixture.bases)
            assertEquals("must survive", fixture.cloud.getString("peerA"))
            assertEquals("keep", fixture.cloud.getString("baseline"))
            assertEquals(42L, fixture.manager.lastAppliedVersion())
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy missing row seeds zero and colliding seed must rederive from winner`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.revision = null
            fixture.beforePut = { if (it == 1) { fixture.cloud = JSONObject().put("peerA", "seed winner"); fixture.revision = 0 } }
            assertTrue(fixture.manager.syncUp())
            assertEquals(listOf(0L, 1L), fixture.attempts)
            assertEquals(listOf(null, 0L), fixture.bases)
            assertEquals("seed winner", fixture.cloud.getString("peerA"))
            assertEquals(1L, fixture.manager.lastAppliedVersion())
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy malformed success is not permission to seed or upload`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            val sealed = requireNotNull(VortXCrypto.sealDocument(fixture.key, fixture.cloud.toString().toByteArray(), fixture.account.id, 40, true))
            for (body in listOf(JSONObject(), JSONObject().put("document", JSONObject.NULL).put("version", 0),
                JSONObject().put("document", sealed), JSONObject().put("document", sealed).put("version", "40"),
                JSONObject().put("document", sealed).put("version", 40.5))) {
                fixture.rawGet = body
                assertFalse(fixture.manager.syncUp())
                assertTrue(fixture.attempts.isEmpty())
                assertEquals(0L, fixture.manager.lastAppliedVersion())
            }
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy failed repull cannot blindly retry a stale derived blob`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.beforePut = { fixture.cloud.put("peerA", "winner"); fixture.revision = 41; fixture.failGet = true }
            assertFalse(fixture.manager.syncUp())
            assertEquals(listOf(41L), fixture.attempts)
            assertEquals(0L, fixture.manager.lastAppliedVersion())
            assertEquals("winner", fixture.cloud.getString("peerA"))
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy authenticated absence creates only at zero with null witness`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.revision = null
            assertTrue(fixture.manager.syncUp())
            assertEquals(listOf(0L), fixture.attempts); assertEquals(listOf<Long?>(null), fixture.bases)
            assertEquals(0L, fixture.revision)
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy existence change never permits stale payload to recreate deleted head`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.beforePut = { if (it == 1) { fixture.revision = null; fixture.cloud = JSONObject() } }
            assertTrue(fixture.manager.syncUp())
            assertEquals(listOf(41L, 0L), fixture.attempts)
            assertEquals(listOf(40L, null), fixture.bases)
            assertFalse(fixture.cloud.has("baseline"))
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy lost response keeps dirty settings until exact fresh candidate is accepted`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.context.getSharedPreferences("vortx_settings", Context.MODE_PRIVATE).edit().putString("stremiox.theme.accent", "fixture-theme").apply()
            fixture.loseAcceptedResponse = true
            assertFalse(fixture.manager.syncUp())
            assertEquals(0L, fixture.manager.lastAppliedVersion())
            val dirty = fixture.context.getSharedPreferences("vortx_sync_dirty", Context.MODE_PRIVATE)
            val dirtyKey = "vortx.sync.dirtySettings.${fixture.account.id}"
            assertTrue(requireNotNull(dirty.getString(dirtyKey, null)).contains("stremiox.theme.accent"))
            fixture.cloud.put("afterLostResponse", "preserve"); fixture.revision = 42
            assertTrue(fixture.manager.syncUp())
            assertEquals(listOf(40L, 42L), fixture.bases)
            assertEquals(listOf(41L, 43L), fixture.attempts)
            assertEquals("preserve", fixture.cloud.getString("afterLostResponse"))
            assertNull(dirty.getString(dirtyKey, null))
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy missing accepted Boolean cannot acknowledge a write`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            for (response in listOf(null, JSONObject(), JSONObject().put("accepted", "true"))) {
                fixture.putFailure = 200 to response
                assertFalse(fixture.manager.syncUp())
                assertEquals(0L, fixture.manager.lastAppliedVersion())
            }
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy upgrade required is terminal account scoped and preserves dirty edits`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.context.getSharedPreferences("vortx_settings", Context.MODE_PRIVATE).edit().putString("stremiox.theme.accent", "fixture-theme").apply()
            fixture.putFailure = 426 to JSONObject().put("error", "upgrade_required").put("accepted", false)
            assertFalse(fixture.manager.syncUp())
            assertEquals(1, fixture.attempts.size)
            assertEquals("Update VortX to keep syncing. Your changes are still on this device.", fixture.manager.syncProblem.value)
            assertEquals(0L, fixture.manager.lastAppliedVersion())
            assertFalse(fixture.manager.syncUp()); assertEquals(1, fixture.attempts.size)
            assertTrue(fixture.context.getSharedPreferences("vortx_sync_dirty", Context.MODE_PRIVATE)
                .getString("vortx.sync.dirtySettings.${fixture.account.id}", null)?.contains("stremiox.theme.accent") == true)
            fixture.manager.replaceSyncSessionTestSeam(VortXSyncManager.Session("second-account-token",
                fixture.account.copy(id = "second-account"), fixture.key))
            fixture.putFailure = null
            assertFalse(fixture.manager.syncUp()) // Old account ciphertext cannot hydrate this new account.
            assertNull(fixture.manager.syncProblem.value)
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy same account reauthentication cannot acknowledge in flight candidate`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.beforePut = { fixture.manager.replaceSyncSessionTestSeam(
                VortXSyncManager.Session("replacement-token", fixture.account, fixture.key)) }
            assertFalse(fixture.manager.syncUp())
            assertEquals(listOf(40L), fixture.bases)
            assertEquals(0L, fixture.manager.lastAppliedVersion())
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy nested extensions survive owned rewrites while known optionals remain deleted`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            val owner = fixture.store.profiles.first { it.isOwner }
            val raw = owner.encode().put("futureProfile", "keep").put("email", "remove@example.invalid")
            raw.put("playback", JSONObject().put("futurePlayback", "keep").put("subSizeScale", 9))
            val summary = JSONObject().put("id", owner.id).put("futureSummary", "keep").put("settings",
                JSONObject().put("futureSetting", "keep").put("playback", JSONObject().put("futurePlayback", "keep").put("subSizeScale", 9)))
            fixture.cloud.put("vortx", JSONObject().put("futureVortx", "keep").put("addonIdentityVersion", 3)
                .put("roster", JSONArray().put(raw)).put("profiles", JSONArray().put(summary)))
            fixture.cloud.put("settings", requireNotNull(SettingsBackup.settingsBlobFor(null, listOf(owner), 0.0,
                "fixture", rawRosterJson = JSONArray().put(owner.encode().put("futureSettingsRoster", "keep")).toString())))
            fixture.store.onRosterPush = {}
            fixture.store.update(owner.copy(email = null, playback = PlaybackPrefs("", "", "", "", "", "", "")))
            assertTrue(fixture.manager.syncUp())
            val vortx = fixture.cloud.getJSONObject("vortx")
            assertEquals("keep", vortx.getString("futureVortx")); assertEquals(3, vortx.getInt("addonIdentityVersion"))
            val profile = vortx.getJSONArray("roster").getJSONObject(0)
            assertEquals("keep", profile.getString("futureProfile")); assertFalse(profile.has("email"))
            assertEquals("keep", profile.getJSONObject("playback").getString("futurePlayback"))
            assertFalse(profile.getJSONObject("playback").has("subSizeScale"))
            val rewrittenSummary = vortx.getJSONArray("profiles").getJSONObject(0)
            assertEquals("keep", rewrittenSummary.getString("futureSummary"))
            assertEquals("keep", rewrittenSummary.getJSONObject("settings").getString("futureSetting"))
            assertEquals("keep", rewrittenSummary.getJSONObject("settings").getJSONObject("playback").getString("futurePlayback"))
            assertFalse(rewrittenSummary.getJSONObject("settings").getJSONObject("playback").has("subSizeScale"))
            val settingsRoster = requireNotNull(SettingsBackup.rawRosterFromBlob(fixture.cloud.opt("settings"))).getJSONObject(0)
            assertEquals("keep", settingsRoster.getString("futureProfile"))
            assertEquals("keep", settingsRoster.getString("futureSettingsRoster"))
            assertFalse(settingsRoster.has("email"))
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }

    @Test fun `legacy overlay siblings and row extensions survive while deleted profile cannot return`() = runBlocking {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val fixture = Fixture()
        try {
            fixture.store.onRosterPush = {}
            val child = UserProfile(id = "00000000-0000-0000-0000-000000000456", name = "Child", avatar = "star")
            fixture.store.add(child)
            fixture.store.applyRemoteOverlay(child.id, mapOf("tt123" to WatchEntry(name = "Local", type = "movie", timeOffsetMs = 7_000)))
            fixture.cloud.put("nativeSync", JSONObject().put("opaqueFuture", "keep"))
            fixture.cloud.put("vortx", JSONObject().put("roster", JSONArray().put(fixture.store.profiles.first { it.isOwner }.encode()).put(child.encode()))
                .put("byProfile", JSONObject().put(child.id, JSONObject().put("futureBucket", "keep").put("library",
                    JSONArray().put(JSONObject().put("id", "tt123").put("futureRow", "keep").put("t", 1))))))
            assertTrue(fixture.manager.syncUp())
            val bucket = fixture.cloud.getJSONObject("vortx").getJSONObject("byProfile").getJSONObject(child.id)
            assertEquals("keep", bucket.getString("futureBucket"))
            assertEquals("keep", bucket.getJSONArray("library").getJSONObject(0).getString("futureRow"))
            assertEquals(7L, bucket.getJSONArray("library").getJSONObject(0).getLong("t"))
            fixture.store.remove(child)
            assertTrue(fixture.manager.syncUp())
            val vortx = fixture.cloud.getJSONObject("vortx")
            assertEquals(1, vortx.getJSONArray("roster").length())
            assertEquals(child.id, vortx.getJSONArray("deletedProfiles").getString(0))
            assertEquals("keep", fixture.cloud.getJSONObject("nativeSync").getString("opaqueFuture"))
        } finally { fixture.manager.cancelSyncTestWork(); Dispatchers.resetMain() }
    }
}
