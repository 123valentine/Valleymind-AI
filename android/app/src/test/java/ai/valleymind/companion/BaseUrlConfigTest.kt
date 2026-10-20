package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BaseUrlConfigTest {

    @Test
    fun `production url is valid and unchanged`() {
        val production = "https://valleymind-ai.onrender.com"
        assertTrue(BaseUrlConfig.isValid(production))
        assertEquals(production, BaseUrlConfig.normalize(production))
        assertFalse(BaseUrlConfig.isLocal(production))
    }

    @Test
    fun `trailing slashes are stripped`() {
        assertEquals("https://valleymind-ai.onrender.com", BaseUrlConfig.normalize("https://valleymind-ai.onrender.com///"))
    }

    @Test
    fun `whitespace is trimmed`() {
        assertEquals("https://valleymind-ai.onrender.com", BaseUrlConfig.normalize("  https://valleymind-ai.onrender.com  "))
    }

    @Test
    fun `scheme is lowercased`() {
        assertEquals("https://valleymind-ai.onrender.com", BaseUrlConfig.normalize("HTTPS://valleymind-ai.onrender.com"))
    }

    @Test
    fun `local lan urls are valid and detected as local`() {
        val lan = "http://192.168.1.40:8000"
        assertTrue(BaseUrlConfig.isValid(lan))
        assertTrue(BaseUrlConfig.isLocal(lan))
        assertEquals("http://192.168.1.40:8000", BaseUrlConfig.normalize(lan))
    }

    @Test
    fun `emulator and loopback hosts are local`() {
        assertTrue(BaseUrlConfig.isLocal("http://10.0.2.2:8000"))
        assertTrue(BaseUrlConfig.isLocal("http://localhost:8000"))
        assertTrue(BaseUrlConfig.isLocal("http://127.0.0.1:8000"))
        assertTrue(BaseUrlConfig.isLocal("http://10.1.2.3:8000"))
        assertTrue(BaseUrlConfig.isLocal("http://172.20.0.5:8000"))
    }

    @Test
    fun `public 172 host is not local`() {
        assertFalse(BaseUrlConfig.isLocal("http://172.5.0.5:8000"))
    }

    @Test
    fun `null blank and malformed inputs are rejected`() {
        assertNull(BaseUrlConfig.normalize(null))
        assertNull(BaseUrlConfig.normalize(""))
        assertNull(BaseUrlConfig.normalize("   "))
        assertNull(BaseUrlConfig.normalize("not a url"))
        assertNull(BaseUrlConfig.normalize("ftp://example.com"))
        assertNull(BaseUrlConfig.normalize("file:///etc/passwd"))
        assertNull(BaseUrlConfig.normalize("javascript:alert(1)"))
        assertNull(BaseUrlConfig.normalize("https://"))
        assertFalse(BaseUrlConfig.isValid(null))
    }

    @Test
    fun `urls with a query or fragment are rejected as bases`() {
        assertNull(BaseUrlConfig.normalize("https://example.com/?a=1"))
        assertNull(BaseUrlConfig.normalize("https://example.com/#x"))
    }
}
