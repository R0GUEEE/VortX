package com.vortx.android.cast

import com.vortx.android.model.Playable
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CastReceiverURLPolicyTest {
    private val hash = "a".repeat(40)
    private val server = "http://192.168.1.20:11470"
    @Test fun `chosen LAN torrent uses actual final server endpoint`() {
        val media = Playable("$server/$hash/0", "movie", isTorrent = true, viaStreamingServer = true)
        assertFalse(CastEligibility.isCastable(media))
        assertTrue(CastEligibility.isCastable(media, server))
        assertFalse(CastEligibility.isCastable(media, "http://192.168.1.21:11470"))
        assertFalse(CastEligibility.isCastable(media.copy(url = "$server/settings"), server))
        assertFalse(CastEligibility.isCastable(media.copy(headers = mapOf("Cookie" to "private")), server))
        assertFalse(CastEligibility.isCastable(media.copy(isTrailer = true), server))
    }
    @Test fun `explicit Tailscale server is LAN authority but add-on URL alone is not`() {
        val tailServer = "http://100.89.1.2:11470"
        val media = Playable("$tailServer/$hash/0", "movie", isTorrent = true, viaStreamingServer = true)
        assertTrue(CastEligibility.isCastable(media, tailServer))
        assertFalse(CastEligibility.isCastable(media))
        assertFalse(CastEligibility.isCastable(media, "http://100.89.1.3:11470"))
        assertFalse(CastEligibility.isRemoteHttpUrl("http://100.100.100.200/$hash/0"))
    }
    @Test fun `public resolved torrent still needs explicit selected server proof`() {
        val media = Playable("https://stream.example/$hash/0", "movie", isTorrent = true)
        assertFalse(CastEligibility.isCastable(media))
        assertTrue(CastEligibility.isCastable(media, "https://stream.example"))
    }
    @Test fun `local metadata and ambiguous numeric hosts remain forbidden even when selected`() {
        listOf("127.1", "127.2.3.4", "127.0.0.1", "2130706433", "0x7f000001", "0177.0.0.1", "0.0.0.0",
            "localhost", "foo.localhost", "169.254.169.254", "metadata.google.internal", "[::1]", "[::]",
            "[fe80::1]", "[::ffff:127.0.0.1]", "[fd00:ec2::254]", "168.63.129.16").forEach { host ->
            val base = "http://$host"
            assertFalse(host, CastEligibility.isRemoteHttpUrl("$base/$hash/0"))
            assertFalse(host, CastEligibility.isCastable(Playable("$base/movie.mp4", "movie")))
            assertFalse(host, CastEligibility.isCastable(Playable("$base/$hash/0", "movie", isTorrent = true), base))
        }
    }
    @Test fun `ordinary self hosted LAN and Tailscale URLs need no streaming server selection`() {
        val urls = listOf(
            "http://192.168.1.20/private.mp4",
            "http://10.0.0.7/library/movie.mkv",
            "https://172.16.0.5/library/movie.mp4",
            "http://100.89.1.2:8080/media/episode.m3u8",
            "http://nas.local/movie.mp4",
            "http://nas/movie.mp4",
            "https://nas.example.ts.net/movie.mkv",
            "http://[fd7a:115c:a1e0::12]/movie.mp4",
        )
        for (url in urls) {
            val media = Playable(url, "movie")
            assertTrue(url, CastEligibility.isRemoteHttpUrl(url))
            assertTrue(url, CastEligibility.isCastable(media))
            // A selected-server value is irrelevant to an ordinary direct/self-hosted stream.
            assertTrue(url, CastEligibility.isCastable(media, "http://192.168.1.99:11470"))
        }
    }

    @Test fun `ordinary LAN and Tailscale routes still refuse receiver unavailable credentials and split media`() {
        for (url in listOf("$server/movie.mp4", "http://100.89.1.2:8080/movie.mp4")) {
            val media = Playable(url, "movie")
            assertFalse(CastEligibility.isCastable(media.copy(headers = mapOf("Authorization" to "Bearer test"))))
            assertFalse(CastEligibility.isCastable(media.copy(headers = mapOf("Cookie" to "test"))))
            assertFalse(CastEligibility.isCastable(media.copy(headers = mapOf("Referer" to "https://addon.example/"))))
            assertFalse(CastEligibility.isCastable(media.copy(audioUrl = "https://audio.example/leg.m4a")))
            assertFalse(CastEligibility.isCastable(media.copy(userAgent = "bound-client")))
            assertFalse(CastEligibility.isCastable(media.copy(isTrailer = true)))
        }
        assertFalse(CastEligibility.isRemoteHttpUrl("https://user:secret@cdn.example/movie.mp4"))
    }

    @Test fun `owned progressive Usenet transport gains no receiver lifetime from a LAN URL`() {
        var closed = false
        val originalLease = AutoCloseable { closed = true }
        val media = Playable("$server/movie.mkv", "movie", playbackLease = originalLease)
        assertFalse(CastEligibility.isCastable(media))
        assertFalse(CastEligibility.isCastable(media, server))
        assertFalse(CastEligibility.isCastable(media.copy(url = "http://127.0.0.1:11470/movie.mkv")))
        assertFalse(closed) // Eligibility neither transfers nor retires a producer's original lifetime.
    }

    @Test fun `torrent authority still requires exact selected origin and media route`() {
        val selected = "https://192.168.1.20:8443/stream"
        val media = Playable("$selected/$hash/0", "movie", isTorrent = true, viaStreamingServer = true)
        assertFalse(CastEligibility.isCastable(media))
        assertTrue(CastEligibility.isCastable(media, selected))
        for (other in listOf("http://192.168.1.20:8443/stream", "https://192.168.1.20:8444/stream",
                "https://192.168.1.21:8443/stream", "https://192.168.1.20:8443/other"))
            assertFalse(other, CastEligibility.isCastable(media, other))
        for (path in listOf("/settings", "/${"a".repeat(39)}/0", "/$hash/-1", "/$hash/0/extra"))
            assertFalse(path, CastEligibility.isCastable(media.copy(url = selected + path), selected))
    }
}
