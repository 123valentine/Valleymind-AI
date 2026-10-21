package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OverlayPageVerifierTest {

    private val production = "https://valleymind-ai-opms.onrender.com"
    private val companionPage = "$production/static/companion_overlay.html"

    @Test
    fun `companion url is built from the base url`() {
        assertEquals(companionPage, OverlayPageVerifier.companionUrl(production))
        assertEquals(
            "http://192.168.1.40:8000/static/companion_overlay.html",
            OverlayPageVerifier.companionUrl("http://192.168.1.40:8000")
        )
    }

    @Test
    fun `companion url is null for an invalid base`() {
        assertNull(OverlayPageVerifier.companionUrl(null))
        assertNull(OverlayPageVerifier.companionUrl("ftp://x"))
        assertNull(OverlayPageVerifier.companionUrl("not a url"))
    }

    @Test
    fun `only the companion page url is accepted`() {
        assertTrue(OverlayPageVerifier.isCompanionPageUrl(companionPage, production))
        assertTrue(OverlayPageVerifier.shouldAllowNavigation(companionPage, production))
    }

    @Test
    fun `trailing slash and scheme casing do not change the verdict`() {
        assertTrue(OverlayPageVerifier.isCompanionPageUrl("$companionPage/", production))
        assertTrue(OverlayPageVerifier.isCompanionPageUrl(
            "HTTPS://VALLEYMIND-AI-OPMS.ONRENDER.COM/static/companion_overlay.html", production))
    }

    @Test
    fun `the app root and index fallback are rejected`() {
        assertFalse(OverlayPageVerifier.shouldAllowNavigation(production, production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("$production/", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("$production/index.html", production))
    }

    @Test
    fun `a different path on the same origin is rejected`() {
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("$production/static/app.js", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("$production/static/cloud.js", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("$production/auth/status", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("$production/vrm-preview/", production))
    }

    @Test
    fun `a different origin is rejected even with the companion path`() {
        assertFalse(OverlayPageVerifier.shouldAllowNavigation(
            "https://evil.example.com/static/companion_overlay.html", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation(
            "http://valleymind-ai-opms.onrender.com/static/companion_overlay.html", production))
    }

    @Test
    fun `another deployment of the companion is rejected for this base`() {
        assertFalse(OverlayPageVerifier.isCompanionPageUrl(
            "http://192.168.1.40:8000/static/companion_overlay.html", production))
    }

    @Test
    fun `null blank and malformed navigation targets are rejected`() {
        assertFalse(OverlayPageVerifier.shouldAllowNavigation(null, production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("   ", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("about:blank", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("javascript:alert(1)", production))
        assertFalse(OverlayPageVerifier.shouldAllowNavigation("data:text/html,<h1>app</h1>", production))
    }

    @Test
    fun `an overlay document that has not loaded yet needs no reload`() {
        assertFalse(OverlayPageVerifier.mustReloadForBaseChange(null, production))
    }

    @Test
    fun `the overlay reloads when the base url changed under a live document`() {
        assertTrue(OverlayPageVerifier.mustReloadForBaseChange(
            "https://valleymind-ai-opms.onrender.com/static/companion_overlay.html",
            "http://192.168.10.181:8000"))
    }

    @Test
    fun `the overlay keeps its document when the base url is unchanged`() {
        assertFalse(OverlayPageVerifier.mustReloadForBaseChange(companionPage, production))
        assertFalse(OverlayPageVerifier.mustReloadForBaseChange("$companionPage/", production))
    }

    @Test
    fun `the page marker requires every companion element`() {
        assertTrue(OverlayPageVerifier.looksLikeCompanionDocument(true))
        assertFalse(OverlayPageVerifier.looksLikeCompanionDocument(false))
    }

    @Test
    fun `page failure is fail-closed on either load failure or missing marker`() {
        assertTrue(OverlayPageVerifier.pageFailed(pageLoadFailed = true, markerVerified = true))
        assertTrue(OverlayPageVerifier.pageFailed(pageLoadFailed = false, markerVerified = false))
        assertTrue(OverlayPageVerifier.pageFailed(pageLoadFailed = true, markerVerified = false))
        assertFalse(OverlayPageVerifier.pageFailed(pageLoadFailed = false, markerVerified = true))
    }

    @Test
    fun `marker js targets the companion page elements`() {
        val js = OverlayPageVerifier.PAGE_MARKER_JS
        assertTrue(js.contains("vmOverlayRoot"))
        assertTrue(js.contains("stage"))
        assertTrue(js.contains("btnHide"))
    }
}
