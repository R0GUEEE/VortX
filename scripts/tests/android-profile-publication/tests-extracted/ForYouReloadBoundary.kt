package com.vortx.android.foryou
import android.content.SharedPreferences
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject
interface ForYouNativeGateway {
    class Snapshot(val profileId: String, val fields: JSONObject, private val admission: (() -> Unit) -> Boolean) {
        fun publishIfCurrent(action: () -> Unit): Boolean = admission(action)
    }
    val changes: Flow<Unit>
    /** The native active profile id and its `forYou.` registers; failure until the account is mounted. */
    fun capture(): Result<Snapshot>
    suspend fun write(profileId: String, field: String, value: Any): Result<Unit>
}

data class ForYouState(
    val profileId: String? = null,
    val interests: ForYouInterests? = null,
    val collections: List<SmartCollection> = emptyList(),
    /** The profile's saved choices are known (the account's native state loaded, or signed out). The
     * interests picker is only offered when ready, so a device that has not yet received another
     * device's answer never asks again. */
    val ready: Boolean = false,
) {
    val homeCollections: List<SmartCollection> get() = collections.filter { it.home }
}

class ExtractedForYouStore internal constructor(
    private val prefs: SharedPreferences,
    private val activeProfileId: () -> String?,
    private val signedIn: () -> Boolean,
    registerProfileSwitch: ((() -> Unit) -> Unit),
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default),
    private val mainDispatcher: CoroutineDispatcher = Dispatchers.Main,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) {
    private val _state = MutableStateFlow(ForYouState())
    val state: StateFlow<ForYouState> = _state.asStateFlow()

    @Volatile private var gateway: ForYouNativeGateway? = null
    private var changesJob: Job? = null
    private val publicationLock = Any()
    private var reloadGeneration = 0L
    private var reloadJob: Job? = null
    private val reloadMutex = Mutex()

    init {
        registerProfileSwitch { reload(invalidate = true) }
        reload(invalidate = true)
    }

    /** Application wiring: the native account gateway, observed for remote and local host changes. */
    internal fun installNativeGateway(native: ForYouNativeGateway?) {
        changesJob?.cancel()
        gateway = native
        changesJob = native?.let { scope.launch { it.changes.collect { reload() } } }
        reload(invalidate = true)
    }

    /** May run under native lifecycle admission: only retire local publication, never capture here. */
    internal fun invalidateNativeAuthority() {
        val old = synchronized(publicationLock) {
            reloadGeneration++
            _state.value = ForYouState()
            reloadJob.also { reloadJob = null }
        }
        old?.cancel()
    }

    /** Synchronous profile listeners only clear/schedule. Native reads and parsing belong to IO. */
    fun reload(invalidate: Boolean = false) {
        val old: Job?
        val job: Job
        synchronized(publicationLock) {
            val generation = ++reloadGeneration
            val native = gateway
            if (invalidate) _state.value = ForYouState()
            old = reloadJob
            job = scope.launch(start = CoroutineStart.LAZY) { reload(generation, native) }
            reloadJob = job
        }
        old?.cancel()
        job.start()
    }

    private suspend fun reload(generation: Long, native: ForYouNativeGateway?) {
        val profile = withContext(mainDispatcher) { activeProfileId() }
        val authenticated = withContext(ioDispatcher) { signedIn() }
        try {
            val captured = withContext(ioDispatcher) {
                reloadMutex.withLock {
                    currentCoroutineContext().ensureActive()
                    synchronized(publicationLock) {
                        check(generation == reloadGeneration && gateway === native) { "For You reload was superseded" }
                    }
                    if (authenticated) checkNotNull(native) { "Account not loaded yet" }.capture().getOrThrow() else null
                }
            }
            val (interests, collections) = withContext(ioDispatcher) {
                ForYouSync.read(if (authenticated) captured?.fields else localFields(profile))
            }
            withContext(mainDispatcher) {
                // Read plain ProfileStore state only on Main and outside authenticated admission.
                if (activeProfileId() != profile || signedIn() != authenticated) return@withContext
                val publication = {
                    synchronized(publicationLock) {
                        if (generation == reloadGeneration && gateway === native) {
                            _state.value = ForYouState(profile, interests, collections,
                                ready = profile != null && (!authenticated || captured?.profileId.equals(profile, ignoreCase = true)))
                        }
                    }
                    Unit
                }
                if (authenticated) {
                    if (profile != null && captured?.profileId.equals(profile, ignoreCase = true)) captured?.publishIfCurrent(publication)
                } else publication()
            }
        } catch (failure: Exception) {
            if (failure is kotlinx.coroutines.CancellationException) throw failure
            clearFailedReload(generation, native, profile)
        } catch (_: LinkageError) {
            clearFailedReload(generation, native, profile)
        }
    }

    private fun clearFailedReload(generation: Long, native: ForYouNativeGateway?, profile: String?) = synchronized(publicationLock) {
        if (generation == reloadGeneration && gateway === native) _state.value = ForYouState(profile)
    }

    private fun localFields(profile:String?):JSONObject? = null
}
