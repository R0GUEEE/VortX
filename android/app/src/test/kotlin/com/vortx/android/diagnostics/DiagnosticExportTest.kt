package com.vortx.android.diagnostics

import java.io.File
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DiagnosticExportTest {
    @Test fun `export re-scrubs every line and keeps only distinct always-on lines`() {
        val probe = "2026-10-13 10:00:00.000 [usenet] route=saved outcome=failed\n" +
            "2026-10-13 10:00:01.000 [legacy] old build wrote tt0903747 raw\n"
        val always = "2026-10-13 10:00:00.000 [usenet] route=saved outcome=failed\n" +
            "2026-10-13 09:00:00.000 [app] launch version=0.5.2\n"
        val body = DiagnosticsLog.exportBody("HEADER", probe, always)
        assertTrue(body.startsWith("HEADER\n"))
        assertFalse(body.contains("0903747"))
        assertEquals(1, body.split("route=saved outcome=failed").size - 1)
        assertTrue(body.contains("[app] launch version=0.5.2"))
        assertTrue(body.contains("===== always-on application diagnostics ====="))
    }

    @Test fun `an empty probe log says how to capture one`() {
        val body = DiagnosticsLog.exportBody("H", "", "")
        assertTrue(body.contains("turn on Diagnostic logging"))
        assertTrue(body.contains("always-on diagnostics log is empty"))
    }

    @Test fun `stored crash reports become readable scrubbed lines`() {
        val crash = JSONObject().put("timestampMs", 0).put("thread", "main").put("type", "java.lang.IllegalStateException")
            .put("message", "failed for https://user:hunter2@host.example/tt0903747")
            .put("stack", JSONArray(List(40) { "com.vortx.Frame.method$it(Frame.kt:$it)" }))
            .put("breadcrumbs", JSONArray(listOf("player: opened")))
            .toString()
        val body = DiagnosticsLog.exportBody("H", "", "", listOf(crash))
        assertTrue(body.contains("===== crash reports (1) ====="))
        assertTrue(body.contains("type=java.lang.IllegalStateException"))
        assertTrue(body.contains("at com.vortx.Frame.method39"))
        assertTrue(body.contains("before: player: opened"))
        assertFalse(body.contains("hunter2") || body.contains("0903747"))
    }

    @Test fun `the export link serves once, only on its capability path`() {
        val server = DiagnosticExportServer(body = { "scrubbed body" }, bindAddress = { InetAddress.getLoopbackAddress() })
        val url = requireNotNull(server.start())
        try {
            assertTrue(url.matches(Regex("""http://127\.0\.0\.1:\d+/diag/[A-Za-z0-9_-]{32}""")))
            val base = url.substringBeforeLast("/")
            assertEquals(404, get("$base/guess").first) // a wrong path neither serves nor spends the link
            val ok = get(url)
            assertEquals(200, ok.first)
            assertEquals("scrubbed body", ok.second)
            Thread.sleep(200)
            assertTrue(server.wasServed)
            assertTrue(runCatching { get(url) }.isFailure || get(url).first != 200) // spent
        } finally { server.stop() }
    }

    @Test fun `each start mints a fresh capability`() {
        val server = DiagnosticExportServer(body = { "b" }, bindAddress = { InetAddress.getLoopbackAddress() })
        try {
            val first = requireNotNull(server.start())
            val second = requireNotNull(server.start())
            assertFalse(first.substringAfter("/diag/") == second.substringAfter("/diag/"))
        } finally { server.stop() }
    }

    @Test fun `the file provider shares only the update and export staging folders`() {
        val xml = listOf(
            File("src/main/res/xml/update_file_paths.xml"),
            File("app/src/main/res/xml/update_file_paths.xml"),
        ).first(File::isFile).readText()
        val paths = Regex("""path="([^"]*)"""").findAll(xml).map { it.groupValues[1] }.toList()
        assertEquals(listOf("update/", "diagnostics-export/"), paths)
        assertFalse(xml.contains("<files-path") || xml.contains("<external") || xml.contains("<root-path"))
    }

    private fun get(url: String): Pair<Int, String> {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = 2_000
        connection.readTimeout = 2_000
        return try {
            val code = connection.responseCode
            code to (if (code == 200) connection.inputStream.bufferedReader().readText() else "")
        } finally { connection.disconnect() }
    }
}
