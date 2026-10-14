package com.vortx.android.network

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import kotlin.concurrent.thread
import java.util.Base64
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Network shares, the same cases as Apple `NetworkSharesTests` + `NetworkShareClientLiveTests`. */
class NetworkSharesTest {
    // Addresses and credential scoping

    @Test fun `addresses are normalized and unsafe ones refused`() {
        assertEquals("http://nas.local:5005/video/", NetworkSharePolicy.rootUrl("nas.local:5005/video"))
        assertEquals("https://NAS.local/dav/", NetworkSharePolicy.rootUrl(" https://NAS.local/dav/ "))
        assertEquals("http://192.168.1.20/", NetworkSharePolicy.rootUrl("192.168.1.20"))
        assertNull(NetworkSharePolicy.rootUrl("http://me:secret@nas.local/"))
        assertNull(NetworkSharePolicy.rootUrl("smb://nas.local/video"))
        assertNull(NetworkSharePolicy.rootUrl("file:///etc/"))
        assertNull(NetworkSharePolicy.rootUrl("http://nas.local/?a=1"))
        assertNull(NetworkSharePolicy.rootUrl("   "))
    }

    @Test fun `the login goes only to files inside the share`() {
        val root = "https://nas.local:5006/video/"
        assertEquals(mapOf("Authorization" to "Basic bWU6cHc="),
            NetworkSharePolicy.headers("https://nas.local:5006/video/Films/a.mkv", root, "me", "pw"))
        for (outside in listOf("https://evil.example/video/a.mkv", "https://nas.local:5001/video/a.mkv",
                "http://nas.local:5006/video/a.mkv", "https://nas.local:5006/videos/a.mkv")) {
            assertTrue(outside, NetworkSharePolicy.headers(outside, root, "me", "pw").isEmpty())
        }
        assertFalse(NetworkSharePolicy.isInside("https://nas.local:5006/video/../photos/x.jpg", root))
        assertTrue(NetworkSharePolicy.headers("https://nas.local:5006/video/a.mkv", root, "", "").isEmpty())
        assertTrue(NetworkSharePolicy.isInside("http://nas.local:80/v/a.mkv", "http://nas.local/v/"))
    }

    // Listings

    @Test fun `WebDAV listings keep only direct children inside the share`() {
        val root = "https://nas.local:5006/video/"
        val directory = "https://nas.local:5006/video/Films/"
        val body = """<?xml version="1.0" encoding="UTF-8"?>
            <D:multistatus xmlns:D="DAV:">
              <D:response><D:href>/video/Films/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop></D:propstat></D:response>
              <D:response><D:href>/video/Films/Dune%20Part%20Two%20(2024)/</D:href><D:propstat><D:prop><D:displayname>Dune Part Two (2024)</D:displayname><D:resourcetype><D:collection/></D:resourcetype></D:prop></D:propstat></D:response>
              <D:response><D:href>https://nas.local:5006/video/Films/Arrival.2016.1080p.mkv</D:href><D:propstat><D:prop><D:getcontentlength>8123456789</D:getcontentlength><D:resourcetype/></D:prop></D:propstat></D:response>
              <D:response><D:href>/video/Films/poster.jpg</D:href><D:propstat><D:prop><D:resourcetype/></D:prop></D:propstat></D:response>
              <D:response><D:href>/video/Films/.DS_Store</D:href><D:propstat><D:prop><D:resourcetype/></D:prop></D:propstat></D:response>
              <D:response><D:href>/video/Films/Raw Name [1080p].mp4</D:href><D:propstat><D:prop><D:resourcetype/></D:prop></D:propstat></D:response>
              <D:response><D:href>https://evil.example/video/Films/x.mkv</D:href><D:propstat><D:prop><D:resourcetype/></D:prop></D:propstat></D:response>
              <D:response><D:href>/photos/private.mkv</D:href><D:propstat><D:prop><D:resourcetype/></D:prop></D:propstat></D:response>
              <D:response><D:href>/video/Films/Deep/Nested/x.mkv</D:href><D:propstat><D:prop><D:resourcetype/></D:prop></D:propstat></D:response>
            </D:multistatus>"""
        val parsed = requireNotNull(NetworkSharePolicy.parseWebDav(body, directory, root))
        assertEquals(5, parsed.size)
        val shown = NetworkSharePolicy.visible(parsed)
        assertEquals(listOf("Dune Part Two (2024)", "Arrival.2016.1080p.mkv", "Raw Name [1080p].mp4"), shown.map { it.name })
        assertTrue(shown[0].isFolder)
        assertEquals(8_123_456_789L, shown[1].size)
        assertEquals("https://nas.local:5006/video/Films/Raw%20Name%20%5B1080p%5D.mp4", shown[2].url)
        assertNull(NetworkSharePolicy.parseWebDav("<html><body>Login</body></html>", directory, root))
        assertNull(NetworkSharePolicy.parseWebDav("""<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><multistatus xmlns="DAV:">&e;</multistatus>""", directory, root))
        assertNull(NetworkSharePolicy.parseWebDav("""<?xml version="1.0"?><!doctype m [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><multistatus xmlns="DAV:"><response><href>&b;</href></response></multistatus>""", directory, root))
        val utf16Declared = """<?xml version="1.0" encoding="UTF-16"?><multistatus xmlns="DAV:"><response><href>/video/Films/a.mkv</href><propstat><prop><resourcetype/></prop></propstat></response></multistatus>"""
        assertEquals("a declared encoding cannot change what the parser reads",
            listOf("a.mkv"), NetworkSharePolicy.parseWebDav(utf16Declared, directory, root)?.map { it.name })
        assertNull(NetworkSharePolicy.parseWebDav("<\u0000!\u0000D\u0000O\u0000C", directory, root))
        val unprefixed = """<multistatus xmlns="DAV:"><response><href>/video/Films/a.mkv</href><propstat><prop><resourcetype/></prop></propstat></response></multistatus>"""
        assertEquals(listOf("a.mkv"), NetworkSharePolicy.parseWebDav(unprefixed, directory, root)?.map { it.name })
    }

    @Test fun `web folder listings from nginx, Apache and python`() {
        val root = "http://192.168.1.20:8080/media/"
        val nginx = """<html><body><pre><a href="../">../</a>
            <a href="Shows/">Shows/</a>
            <a href="The%20Matrix%20(1999).mkv">The Matrix (1999).mkv</a>
            <a href="notes.txt">notes.txt</a></pre></body></html>"""
        assertEquals(listOf("Shows", "The Matrix (1999).mkv"),
            NetworkSharePolicy.visible(NetworkSharePolicy.parseHtmlIndex(nginx, root, root)).map { it.name })
        val apache = """<a href="?C=N;O=D">Name</a><a href="/">Parent Directory</a><a href="Movies/">Movies/</a>
            <a href="/media/clip.MP4">clip.MP4</a><a href="http://other.example/media/x.mkv">x</a><a href="Movies/inner.mkv">i</a>"""
        assertEquals(listOf("Movies", "clip.MP4"),
            NetworkSharePolicy.visible(NetworkSharePolicy.parseHtmlIndex(apache, root, root)).map { it.name })
        val python = """<ul><li><a href="Season%201/">Season 1/</a></li><li><a href='E01.mkv'>E01.mkv</a></li><li><a href="E01.mkv">dup</a></li></ul>"""
        val entries = NetworkSharePolicy.parseHtmlIndex(python, root, root)
        assertEquals(listOf("Season 1", "E01.mkv"), entries.map { it.name })
        assertTrue(entries[0].isFolder && entries[0].url.endsWith("/media/Season%201/"))
    }

    @Test fun `episodes sort in natural order`() {
        val names = listOf("E10.mkv", "E2.mkv", "e1.mkv")
        assertEquals(listOf("e1.mkv", "E2.mkv", "E10.mkv"),
            NetworkSharePolicy.visible(names.map { NetworkShareEntry(it, "http://h/$it", false, null) }).map { it.name })
    }

    // The client against a local fixture server

    private lateinit var server: ServerSocket
    private var port = 0
    private val auth = "Basic " + Base64.getEncoder().encodeToString("me:pw".toByteArray())
    private val tree = mapOf(
        "/dav/" to listOf("Films/", "Arrival (2016).mkv", "poster.jpg"),
        "/dav/Films/" to listOf("Dune Part Two.mkv"),
        "/web/" to listOf("Shows/", "The Matrix (1999).mp4", "notes.txt"),
        "/web/Shows/" to listOf("S01E01.mkv"),
    )

    /** A minimal HTTP/1.1 fixture (one request per connection): the JDK server is not on the Android test classpath. */
    @Before fun startServer() {
        server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        port = server.localPort
        thread(isDaemon = true) {
            while (!server.isClosed) {
                val socket = runCatching { server.accept() }.getOrNull() ?: break
                thread(isDaemon = true) { socket.use { serve(it) } }
            }
        }
    }

    @After fun stopServer() = server.close()

    private fun serve(socket: Socket) {
        val reader = BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.ISO_8859_1))
        val requestLine = reader.readLine() ?: return
        val headers = HashMap<String, String>()
        while (true) {
            val line = reader.readLine() ?: break
            if (line.isEmpty()) break
            headers[line.substringBefore(':').trim().lowercase()] = line.substringAfter(':').trim()
        }
        repeat(headers["content-length"]?.toIntOrNull() ?: 0) { reader.read() }
        val (method, target) = requestLine.split(' ').let { it[0] to it[1] }
        val (status, body, extra) = route(method, URLDecoder.decode(target.substringBefore('?'), "UTF-8"), headers["authorization"],
            headers["range"])
        val bytes = if (method == "HEAD") ByteArray(0) else body.toByteArray()
        val head = buildString {
            append("HTTP/1.1 $status X\r\n")
            extra.forEach { (k, v) -> append("$k: $v\r\n") }
            append("Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n")
        }
        socket.getOutputStream().apply { write(head.toByteArray(Charsets.ISO_8859_1)); write(bytes); flush() }
    }

    private fun route(method: String, path: String, authorization: String?, range: String?): Triple<Int, String, Map<String, String>> {
        // Video files for the playback probe; /dav/ refuses HEAD so the client falls back to a ranged GET.
        if (path == "/web/moved.mkv") return Triple(302, "", mapOf("Location" to "/web/The%20Matrix%20(1999).mp4"))
        if (path == "/web/go-away.mkv") return Triple(302, "", mapOf("Location" to "http://elsewhere.invalid/x.mkv"))
        if (path == "/web/The Matrix (1999).mp4" || path == "/dav/Arrival (2016).mkv") {
            if (authorization != auth) return Triple(401, "", emptyMap())
            if (method == "HEAD" && path.startsWith("/dav/")) return Triple(405, "", emptyMap())
            return if (range == "bytes=0-0") Triple(206, "0", emptyMap()) else Triple(200, "0123456789", emptyMap())
        }
        if (path == "/dav/away/") return Triple(302, "", mapOf("Location" to "http://elsewhere.invalid/dav/"))
        if (path == "/web/hop/") return Triple(302, "", mapOf("Location" to "/web/Shows/"))
        val dav = path.startsWith("/dav/")
        if ((method == "PROPFIND") != dav) return Triple(405, "", emptyMap())
        if (authorization != auth) return Triple(401, "", emptyMap())
        val children = tree[path] ?: return Triple(404, "", emptyMap())
        return if (dav) {
            val rows = children.joinToString("") { name ->
                val folder = name.endsWith("/")
                "<D:response><D:href>${path}${name.replace(" ", "%20")}</D:href><D:propstat><D:prop><D:resourcetype>" +
                    (if (folder) "<D:collection/>" else "") + "</D:resourcetype></D:prop></D:propstat></D:response>"
            }
            Triple(207, """<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">$rows</D:multistatus>""", emptyMap())
        } else {
            val links = children.joinToString("\n") { "<a href=\"${it.replace(" ", "%20")}\">$it</a>" }
            Triple(200, "<html><body><pre><a href=\"../\">../</a>\n$links</pre></body></html>", emptyMap())
        }
    }

    @Test fun `the client detects, lists and keeps the login inside the share`() = runBlocking {
        val client = NetworkShareClient()
        val good = NetworkShareClient.Login("me", "pw")
        val dav = requireNotNull(NetworkSharePolicy.rootUrl("127.0.0.1:$port/dav"))
        val web = requireNotNull(NetworkSharePolicy.rootUrl("127.0.0.1:$port/web"))
        assertEquals(NetworkShareKind.WEBDAV, client.detect(dav, good))
        assertEquals(NetworkShareKind.HTTP, client.detect(web, good))
        assertEquals(NetworkShareException.Reason.UNAUTHORIZED,
            runCatching { client.detect(dav, NetworkShareClient.Login("me", "nope")) }.exceptionOrNull().let { (it as? NetworkShareException)?.reason })
        assertEquals(NetworkShareException.Reason.UNREACHABLE,
            runCatching { client.detect("http://127.0.0.1:1/x/", good) }.exceptionOrNull().let { (it as? NetworkShareException)?.reason })
        val davRoot = client.list(dav, NetworkShareKind.WEBDAV, dav, good)
        assertEquals(listOf("Films", "Arrival (2016).mkv"), davRoot.map { it.name })
        assertEquals(listOf("Dune Part Two.mkv"), client.list(davRoot[0].url, NetworkShareKind.WEBDAV, dav, good).map { it.name })
        assertEquals(listOf("Shows", "The Matrix (1999).mp4"), client.list(web, NetworkShareKind.HTTP, web, good).map { it.name })
        assertEquals(listOf("S01E01.mkv"), client.list("${web}hop/", NetworkShareKind.HTTP, web, good).map { it.name })
        assertEquals(NetworkShareException.Reason.NOT_A_SHARE,
            runCatching { client.list("${dav}away/", NetworkShareKind.WEBDAV, dav, good) }.exceptionOrNull().let { (it as? NetworkShareException)?.reason })

        // Playback: the player only ever gets an address inside the share.
        val matrix = "${web}The%20Matrix%20(1999).mp4"
        assertEquals(matrix, client.resolveForPlayback(matrix, web, good))
        assertEquals(matrix, client.resolveForPlayback("${web}moved.mkv", web, good))
        assertEquals(NetworkShareException.Reason.REDIRECTED_AWAY,
            runCatching { client.resolveForPlayback("${web}go-away.mkv", web, good) }.exceptionOrNull().let { (it as? NetworkShareException)?.reason })
        val arrival = "${dav}Arrival%20(2016).mkv"
        assertEquals(arrival, client.resolveForPlayback(arrival, dav, good))
        assertEquals(NetworkShareException.Reason.UNAUTHORIZED,
            runCatching { client.resolveForPlayback(matrix, web, NetworkShareClient.Login("me", "nope")) }.exceptionOrNull().let { (it as? NetworkShareException)?.reason })
    }
}
