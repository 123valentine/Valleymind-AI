package ai.valleymind.companion

/**
 * Decides which backend the app is ALLOWED to talk to.
 *
 * Production (release) builds are locked to the build-time origin
 * ([BuildConfig.VALLEY_BASE_URL]); a persisted override or an Intent extra is
 * ignored and cleared. Debug builds may still point at a local backend for
 * development. Keeping this decision in one pure object means the rule is unit
 * tested instead of re-implemented in every Activity/Service.
 */
object ServerPolicy {

    /**
     * Resolves the active base URL. In release builds [storedOverride] is
     * ignored entirely; in debug builds a valid override wins.
     */
    fun resolve(defaultBaseUrl: String, storedOverride: String?, isDebugBuild: Boolean): String {
        val fallback = BaseUrlConfig.normalize(defaultBaseUrl) ?: defaultBaseUrl.trim()
        if (!isDebugBuild) return fallback
        return BaseUrlConfig.normalize(storedOverride) ?: fallback
    }

    /** True when a release build must erase a previously persisted override. */
    fun hasStoredOverrideToClear(isDebugBuild: Boolean, storedOverride: String?): Boolean =
        !isDebugBuild && !storedOverride.isNullOrBlank()

    /** The Change-server UI is a development-only affordance. */
    fun mayChangeServer(isDebugBuild: Boolean): Boolean = isDebugBuild

    /**
     * True only for the single configured origin (http is accepted only when
     * [isDebugBuild]). This is the call-time guard used by the native bridge.
     */
    fun isTrustedPage(url: String?, activeBaseUrl: String, isDebugBuild: Boolean): Boolean =
        TrustedOrigin.isTrusted(url, activeBaseUrl, allowHttp = isDebugBuild)

    /** External HTTP(S) links may be handed to the system browser. */
    fun isExternalNavigationAllowed(url: String?): Boolean {
        val origin = TrustedOrigin.parse(url) ?: return false
        return origin.scheme == "https" || origin.scheme == "http"
    }
}
