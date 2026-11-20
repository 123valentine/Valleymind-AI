package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerPolicyTest {

    private val production = "https://valleymind-ai-opms.onrender.com"
    private val local = "http://192.168.1.40:8000"

    @Test
    fun `release builds ignore and clear a stored override`() {
        assertEquals(production, ServerPolicy.resolve(production, local, isDebugBuild = false))
        assertTrue(ServerPolicy.hasStoredOverrideToClear(isDebugBuild = false, storedOverride = local))
        assertFalse(ServerPolicy.mayChangeServer(isDebugBuild = false))
    }

    @Test
    fun `release builds with no override use the build default`() {
        assertEquals(production, ServerPolicy.resolve(production, null, isDebugBuild = false))
        assertFalse(ServerPolicy.hasStoredOverrideToClear(isDebugBuild = false, storedOverride = null))
        assertFalse(ServerPolicy.hasStoredOverrideToClear(isDebugBuild = false, storedOverride = "  "))
    }

    @Test
    fun `debug builds honour a valid override`() {
        assertEquals(local, ServerPolicy.resolve(production, local, isDebugBuild = true))
        assertTrue(ServerPolicy.mayChangeServer(isDebugBuild = true))
        assertFalse(ServerPolicy.hasStoredOverrideToClear(isDebugBuild = true, storedOverride = local))
    }

    @Test
    fun `debug builds reject an invalid override and keep the default`() {
        assertEquals(production, ServerPolicy.resolve(production, "not a url", isDebugBuild = true))
        assertEquals(production, ServerPolicy.resolve(production, "javascript:alert(1)", isDebugBuild = true))
    }

    @Test
    fun `the default url is normalized`() {
        assertEquals(production, ServerPolicy.resolve("$production///", null, isDebugBuild = false))
    }

    @Test
    fun `the bridge trusts exactly the active origin`() {
        assertTrue(ServerPolicy.isTrustedPage("$production/settings", production, isDebugBuild = false))
        assertFalse(ServerPolicy.isTrustedPage("https://valleymind-ai-opms.onrender.com.evil.example", production, isDebugBuild = false))
        assertFalse(ServerPolicy.isTrustedPage("http://valleymind-ai-opms.onrender.com", production, isDebugBuild = false))
        assertTrue(ServerPolicy.isTrustedPage("$local/", local, isDebugBuild = true))
    }

    @Test
    fun `external navigation is limited to http and https`() {
        assertTrue(ServerPolicy.isExternalNavigationAllowed("https://example.com/page"))
        assertTrue(ServerPolicy.isExternalNavigationAllowed("http://example.com/page"))
        assertFalse(ServerPolicy.isExternalNavigationAllowed("javascript:alert(1)"))
        assertFalse(ServerPolicy.isExternalNavigationAllowed("intent://scan/#Intent;scheme=zxing;end"))
        assertFalse(ServerPolicy.isExternalNavigationAllowed("file:///etc/passwd"))
        assertFalse(ServerPolicy.isExternalNavigationAllowed(null))
    }
}
