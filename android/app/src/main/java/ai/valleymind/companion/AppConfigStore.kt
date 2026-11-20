package ai.valleymind.companion

import android.content.Context

/**
 * Persists an OPTIONAL runtime base-URL override in SharedPreferences so a
 * local backend (e.g. http://192.168.1.40:8000) can be targeted without
 * rebuilding the APK. When no override is stored the build-time production URL
 * is used, so the hosted app and its authentication are unchanged.
 */
class AppConfigStore(context: Context) {

    private val prefs =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** The base URL the WebView should load right now. */
    fun getBaseUrl(defaultBaseUrl: String): String =
        getBaseUrlOverride() ?: BaseUrlConfig.normalize(defaultBaseUrl) ?: defaultBaseUrl

    fun getBaseUrlOverride(): String? = BaseUrlConfig.normalize(prefs.getString(KEY_BASE_URL, null))

    fun hasOverride(): Boolean = getBaseUrlOverride() != null

    /** @return true when the value was valid and has been stored. */
    fun setBaseUrlOverride(raw: String): Boolean {
        val normalized = BaseUrlConfig.normalize(raw) ?: return false
        prefs.edit().putString(KEY_BASE_URL, normalized).apply()
        return true
    }

    fun clearBaseUrlOverride() {
        prefs.edit().remove(KEY_BASE_URL).apply()
    }

    // ── floating companion placement (drag persistence) ──────────────

    /** The last clamped overlay window position, or null on a fresh install. */
    fun getOverlayPosition(): Pair<Int, Int>? {
        val x = prefs.getString(KEY_OVERLAY_X, null)?.toIntOrNull() ?: return null
        val y = prefs.getString(KEY_OVERLAY_Y, null)?.toIntOrNull() ?: return null
        return x to y
    }

    fun setOverlayPosition(x: Int, y: Int) {
        prefs.edit().putString(KEY_OVERLAY_X, x.toString()).putString(KEY_OVERLAY_Y, y.toString()).apply()
    }

    /** The compact, pointer-safe window size requested by the companion page. */
    fun getOverlayRegionDp(): Pair<Int, Int>? {
        val w = prefs.getString(KEY_OVERLAY_W, null)?.toIntOrNull() ?: return null
        val h = prefs.getString(KEY_OVERLAY_H, null)?.toIntOrNull() ?: return null
        return w to h
    }

    fun setOverlayRegionDp(widthDp: Int, heightDp: Int) {
        prefs.edit()
            .putString(KEY_OVERLAY_W, widthDp.toString())
            .putString(KEY_OVERLAY_H, heightDp.toString())
            .apply()
    }

    companion object {
        private const val PREFS_NAME = "vm_app_config"
        private const val KEY_BASE_URL = "base_url_override"
        private const val KEY_OVERLAY_X = "overlay_x"
        private const val KEY_OVERLAY_Y = "overlay_y"
        private const val KEY_OVERLAY_W = "overlay_width_dp"
        private const val KEY_OVERLAY_H = "overlay_height_dp"
    }
}
