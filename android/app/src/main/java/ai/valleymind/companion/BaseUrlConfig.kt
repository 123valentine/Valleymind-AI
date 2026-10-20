package ai.valleymind.companion

import java.net.URI

/**
 * Base-URL helpers with NO Android dependencies so they can be unit tested on
 * the JVM.
 *
 * The WebView loads the ValleyMind web app from a single origin. That origin is
 * the build-time production URL by default, but it can be overridden at runtime
 * (see AppConfigStore) to point at a local backend — for example
 * http://192.168.1.40:8000 — while testing on the same Wi-Fi network. The
 * production URL and the session cookie/auth flow are unaffected when no
 * override is set.
 */
object BaseUrlConfig {

    /** Returns a normalized (canonical) base URL, or null when unusable. */
    fun normalize(raw: String?): String? {
        if (raw.isNullOrBlank()) return null
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) return null
        val uri = try {
            URI(trimmed)
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return null
        val host = uri.host?.lowercase()
        if (host.isNullOrEmpty()) return null
        if (uri.query != null || uri.fragment != null) return null
        val port = if (uri.port != -1) ":${uri.port}" else ""
        // Scheme and host are case-insensitive; the path is not, so keep it as-is.
        val path = (uri.rawPath ?: "").trimEnd('/')
        return "$scheme://$host$port$path"
    }

    fun isValid(raw: String?): Boolean = normalize(raw) != null

    /** True for loopback / private-LAN hosts (local backend testing). */
    fun isLocal(raw: String?): Boolean {
        val normalized = normalize(raw) ?: return false
        val host = try {
            URI(normalized).host?.lowercase()
        } catch (e: Exception) {
            null
        } ?: return false
        return isLocalHost(host)
    }

    private fun isLocalHost(host: String): Boolean {
        if (host == "localhost" || host == "127.0.0.1" || host == "10.0.2.2") return true
        if (host.startsWith("192.168.")) return true
        if (host.startsWith("10.")) return true
        // 172.16.0.0 – 172.31.255.255
        val second = host.split(".").getOrNull(1)?.toIntOrNull()
        if (host.startsWith("172.") && second != null && second in 16..31) return true
        return false
    }
}
