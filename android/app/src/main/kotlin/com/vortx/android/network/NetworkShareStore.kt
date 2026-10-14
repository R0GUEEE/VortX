package com.vortx.android.network

import android.content.Context
import com.vortx.android.security.FailClosedCredentialStore
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject

/**
 * The network shares saved on this device (Apple `NetworkShareStore`). The non-secret list lives in the shared
 * `vortx_settings` file under `vortx.networkShares.list` (excluded from settings sync, see SettingsBackup); each
 * password lives only in the encrypted credential store under `vortx.networkshare.<id>.password`. Device-local
 * by design: a share is an address on this network.
 */
class NetworkShareStore private constructor(context: Context, private val client: NetworkShareClient = NetworkShareClient()) {
    private val prefs = context.applicationContext.getSharedPreferences(SETTINGS_FILE, Context.MODE_PRIVATE)
    private val secrets = FailClosedCredentialStore(context, ENCRYPTED_FILE, tag = TAG)
    private val _shares = MutableStateFlow(load())
    val shares: StateFlow<List<NetworkShare>> = _shares.asStateFlow()

    /** Checks the address, works out WebDAV or web folder, and saves the share once its root actually lists. */
    suspend fun add(name: String, address: String, username: String, password: String): NetworkShare {
        val root = NetworkSharePolicy.rootUrl(address) ?: throw NetworkShareException(NetworkShareException.Reason.INVALID_ADDRESS)
        val user = username.trim()
        val kind = client.detect(root, NetworkShareClient.Login(user, if (user.isEmpty()) "" else password))
        val share = NetworkShare(
            id = UUID.randomUUID().toString(),
            name = name.trim().ifEmpty { runCatching { java.net.URI(root).host }.getOrNull() ?: root },
            kind = kind, url = root, username = user, addedAtMs = System.currentTimeMillis(),
        )
        if (user.isNotEmpty() && !secrets.set(passwordKey(share.id), password)) {
            throw NetworkShareException(NetworkShareException.Reason.CREDENTIAL_STORAGE)
        }
        _shares.value = listOf(share) + _shares.value
        save()
        return share
    }

    fun remove(share: NetworkShare) {
        secrets.clear(passwordKey(share.id))
        _shares.value = _shares.value.filterNot { it.id == share.id }
        save()
    }

    /** The visible contents of [folder] (the share root when null). */
    suspend fun list(share: NetworkShare, folder: String? = null): List<NetworkShareEntry> =
        client.list(folder ?: share.url, share.kind, share.url, login(share))

    /** What the player sends with a file from this share: the login, and only for a file inside it. */
    fun playbackHeaders(share: NetworkShare, url: String): Map<String, String> {
        val login = login(share)
        return NetworkSharePolicy.headers(url, share.url, login.username, login.password)
    }

    private fun login(share: NetworkShare): NetworkShareClient.Login =
        if (share.username.isEmpty()) NetworkShareClient.Login("", "")
        else NetworkShareClient.Login(share.username, secrets.string(passwordKey(share.id)).orEmpty())

    private fun load(): List<NetworkShare> {
        val raw = prefs.getString(LIST_KEY, null) ?: return emptyList()
        val array = runCatching { JSONArray(raw) }.getOrNull() ?: return emptyList()
        return (0 until array.length()).mapNotNull { index ->
            val o = array.optJSONObject(index) ?: return@mapNotNull null
            val kind = NetworkShareKind.fromStorage(o.optString("kind")) ?: return@mapNotNull null
            val url = NetworkSharePolicy.rootUrl(o.optString("url")) ?: return@mapNotNull null
            NetworkShare(o.optString("id").ifEmpty { return@mapNotNull null }, o.optString("name"), kind, url,
                o.optString("username"), o.optLong("addedAtMs"))
        }
    }

    private fun save() {
        val array = JSONArray()
        _shares.value.forEach { share ->
            array.put(JSONObject().put("id", share.id).put("name", share.name).put("kind", share.kind.storageValue)
                .put("url", share.url).put("username", share.username).put("addedAtMs", share.addedAtMs))
        }
        prefs.edit().putString(LIST_KEY, array.toString()).apply()
    }

    companion object {
        const val LIST_KEY = "vortx.networkShares.list"
        private const val SETTINGS_FILE = "vortx_settings"
        private const val ENCRYPTED_FILE = "vortx_network_shares"
        private const val TAG = "NetworkShareStore"
        private fun passwordKey(id: String) = "vortx.networkshare.${id.lowercase()}.password"

        @Volatile private var instance: NetworkShareStore? = null

        fun shared(context: Context): NetworkShareStore =
            instance ?: synchronized(this) { instance ?: NetworkShareStore(context.applicationContext).also { instance = it } }
    }
}
