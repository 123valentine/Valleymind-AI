package ai.valleymind.companion

import java.net.URI

/**
 * Strict origin matching for pages that are allowed to see the native bridge.
 *
 * The privileged bridge is attached to the WebView, so its JavaScript object is
 * visible to EVERY document that WebView renders. A page can therefore call
 * native methods directly, without the injected shim and without honouring any
 * JavaScript-side checks. Trust is enforced here, in Kotlin, by comparing the
 * document URL with the configured backend origin.
 *
 * Rules:
 *  - scheme, host and effective port must match exactly;
 *  - https is required unless [isTrusted] explicitly allows http (debug builds
 *    pointing at a local backend);
 *  - lookalike hosts, user-info tricks, explicit non-default ports, trailing
 *    dots, malformed URLs and non-http(s) schemes are rejected.
 *
 * The comparison ignores path, query and fragment: the app is one origin.
 */
object TrustedOrigin {

    const val HTTPS_PORT = 443
    const val HTTP_PORT = 80

    data class Origin(val scheme: String, val host: String, val port: Int) {
        override fun toString(): String {
            val default = if (scheme == "https") HTTPS_PORT else HTTP_PORT
            return if (port == default) "$scheme://$host" else "$scheme://$host:$port"
        }
    }

    /**
     * Parses [url] into a normalized origin, or returns null when the URL is
     * unusable or deliberately ambiguous.
     */
    fun parse(url: String?): Origin? {
        if (url.isNullOrBlank()) return null
        val trimmed = url.trim()
        if (trimmed.isEmpty()) return null
        val uri = try {
            URI(trimmed)
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "https" && scheme != "http") return null
        if (uri.isOpaque) return null

        // userinfo (@) turns the authority into a redirect-looking URL and is
        // never part of a legitimate app origin.
        val authority = uri.rawAuthority ?: return null
        if (authority.contains('@') || uri.rawUserInfo != null) return null
        if (authority.any { it.isWhitespace() || it == '\\' }) return null

        val host = uri.host?.lowercase() ?: return null
        if (host.isEmpty()) return null
        // A trailing dot is DNS-equivalent to the plain host but compares
        // differently in allowlists; reject it instead of normalizing.
        if (host.endsWith(".")) return null

        val explicitPort = uri.port
        if (explicitPort == 0 || explicitPort > 65535) return null
        // "host:" / "host:abc" parse with no usable port. Only IPv6 literals
        // may contain ':' without an explicit port.
        if (explicitPort == -1 && authority.contains(':') && !host.startsWith("[")) return null

        val port = when {
            explicitPort != -1 -> explicitPort
            scheme == "https" -> HTTPS_PORT
            else -> HTTP_PORT
        }
        return Origin(scheme, host, port)
    }

    /** True when [url] is the same origin as [trustedBaseUrl]. */
    fun isTrusted(url: String?, trustedBaseUrl: String?, allowHttp: Boolean): Boolean {
        val target = parse(url) ?: return false
        val trusted = parse(trustedBaseUrl) ?: return false
        if (!allowHttp && (target.scheme != "https" || trusted.scheme != "https")) return false
        return target == trusted
    }

    fun sameOrigin(first: String?, second: String?): Boolean {
        val a = parse(first) ?: return false
        val b = parse(second) ?: return false
        return a == b
    }
}
