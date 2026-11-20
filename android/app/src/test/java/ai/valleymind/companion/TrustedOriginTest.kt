package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TrustedOriginTest {

    private val production = "https://valleymind-ai-opms.onrender.com"

    @Test
    fun `the exact production origin is trusted`() {
        assertTrue(TrustedOrigin.isTrusted(production, production, allowHttp = false))
        assertEquals(production, TrustedOrigin.parse(production)?.toString())
    }

    @Test
    fun `same origin paths queries and fragments stay trusted`() {
        assertTrue(TrustedOrigin.isTrusted("$production/", production, allowHttp = false))
        assertTrue(TrustedOrigin.isTrusted("$production/settings?tab=voice", production, allowHttp = false))
        assertTrue(TrustedOrigin.isTrusted("$production/#/chat", production, allowHttp = false))
    }

    @Test
    fun `an explicit default port is the same origin`() {
        assertTrue(TrustedOrigin.isTrusted("$production:443/chat", production, allowHttp = false))
    }

    @Test
    fun `plain http is rejected for a release build`() {
        assertFalse(TrustedOrigin.isTrusted("http://valleymind-ai-opms.onrender.com", production, allowHttp = false))
    }

    @Test
    fun `a debug build may use a local http backend and nothing else`() {
        val local = "http://192.168.1.40:8000"
        assertTrue(TrustedOrigin.isTrusted("$local/settings", local, allowHttp = true))
        assertFalse(TrustedOrigin.isTrusted("http://192.168.1.41:8000", local, allowHttp = true))
        // Debug never downgrades the production origin to http.
        assertFalse(TrustedOrigin.isTrusted("http://valleymind-ai-opms.onrender.com", production, allowHttp = true))
    }

    @Test
    fun `lookalike hosts are rejected`() {
        val lookalikes = listOf(
            "https://valleymind-ai-opms.onrender.com.evil.example/",
            "https://valleymind-ai-opms.onrender.com.evil.example",
            "https://valleymind-ai-opms.onrender.co",
            "https://evil.example/valleymind-ai-opms.onrender.com",
            "https://valleymind-ai-opms.onrender.com@evil.example",
            "https://valleymind-ai-opms.onrender.com.",
        )
        for (url in lookalikes) {
            assertFalse("must reject $url", TrustedOrigin.isTrusted(url, production, allowHttp = false))
        }
    }

    @Test
    fun `unexpected ports are rejected`() {
        assertFalse(TrustedOrigin.isTrusted("$production:8443", production, allowHttp = false))
        assertFalse(TrustedOrigin.isTrusted("$production:80", production, allowHttp = false))
    }

    @Test
    fun `userinfo is rejected`() {
        assertFalse(TrustedOrigin.isTrusted("https://user:pass@valleymind-ai-opms.onrender.com", production, allowHttp = false))
    }

    @Test
    fun `malformed and non http schemes are rejected`() {
        for (url in listOf(
            null,
            "",
            "   ",
            "not a url",
            "https://",
            "https://:443",
            "https://host:abc",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "content://ai.valleymind/secret",
            "intent://scan/#Intent;scheme=zxing;end",
        )) {
            assertNull("must reject $url", TrustedOrigin.parse(url))
            assertFalse(TrustedOrigin.isTrusted(url, production, allowHttp = true))
        }
    }

    @Test
    fun `scheme and host are case insensitive`() {
        val parsed = TrustedOrigin.parse("HTTPS://VALLEYmind-AI-OPMS.ONRENDER.COM/Chat")
        assertEquals("https", parsed?.scheme)
        assertEquals("valleymind-ai-opms.onrender.com", parsed?.host)
        assertEquals(443, parsed?.port)
    }

    @Test
    fun `same origin helper is exact`() {
        assertTrue(TrustedOrigin.sameOrigin(production, "$production/x"))
        assertFalse(TrustedOrigin.sameOrigin(production, "$production.evil.example"))
    }
}
