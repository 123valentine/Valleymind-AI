package ai.valleymind.companion

import java.net.URI

/**
 * Fail-closed rules for the floating companion overlay.
 *
 * The overlay window must ONLY ever display the companion page
 * (/static/companion_overlay.html). The ValleyMind backend serves a catch-all
 * route that falls back to index.html for unmatched paths, so a missing or
 * stale companion page would otherwise put the ENTIRE app inside the floating
 * window — the exact "the whole app floats" bug. These helpers make that
 * impossible: the overlay accepts exactly one URL, blocks every other
 * navigation, and verifies the loaded document really is the companion page.
 */
object OverlayPageVerifier {

    const val COMPANION_PATH = "/static/companion_overlay.html"

    /** The one and only URL the overlay is allowed to load. */
    fun companionUrl(baseUrl: String?): String? {
        val base = BaseUrlConfig.normalize(baseUrl) ?: return null
        return base + COMPANION_PATH
    }

    fun isCompanionPageUrl(url: String?, baseUrl: String?): Boolean {
        val expected = companionUrl(baseUrl) ?: return false
        val actual = parts(url) ?: return false
        val wanted = parts(expected) ?: return false
        return actual == wanted
    }

    /** Top-level navigation is allowed only to the companion page itself. */
    fun shouldAllowNavigation(url: String?, baseUrl: String?): Boolean =
        isCompanionPageUrl(url, baseUrl)

    /**
     * True when the document the overlay is already showing belongs to a
     * DIFFERENT base URL than the configured one (Change server while the
     * overlay is up). The overlay must reload in that case, or the floating
     * companion and the main app would keep pointing at different backends.
     * A null URL means "nothing loaded yet", which needs no reload.
     */
    fun mustReloadForBaseChange(loadedUrl: String?, baseUrl: String?): Boolean =
        loadedUrl != null && !isCompanionPageUrl(loadedUrl, baseUrl)

    /**
     * JS evaluated in the loaded document. True only for the real companion
     * page; index.html (the catch-all fallback) has none of these markers.
     */
    val PAGE_MARKER_JS: String =
        "(function(){return !!(document.getElementById('vmOverlayRoot')" +
            " && document.getElementById('stage')" +
            " && document.getElementById('btnHide'));})()"

    fun looksLikeCompanionDocument(markerJsResult: Boolean): Boolean = markerJsResult

    /** Overall failure decision: any load failure OR an unverified document. */
    fun pageFailed(pageLoadFailed: Boolean, markerVerified: Boolean): Boolean =
        pageLoadFailed || !markerVerified

    private data class Parts(
        val scheme: String,
        val host: String,
        val port: Int,
        val path: String
    )

    private fun parts(raw: String?): Parts? {
        if (raw.isNullOrBlank()) return null
        val uri = try {
            URI(raw.trim())
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase() ?: return null
        if (host.isEmpty()) return null
        val port = if (uri.port != -1) uri.port else if (scheme == "https") 443 else 80
        var path = uri.rawPath ?: "/"
        if (path.length > 1 && path.endsWith("/")) path = path.dropLast(1)
        if (path.isEmpty()) path = "/"
        return Parts(scheme, host, port, path)
    }
}
