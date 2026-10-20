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

    companion object {
        private const val PREFS_NAME = "vm_app_config"
        private const val KEY_BASE_URL = "base_url_override"
    }
}
