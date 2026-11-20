package ai.valleymind.companion

/**
 * CameraResultRegistry — delivers an asynchronous camera-frame result back to
 * the WebView that requested it.
 *
 * CameraX emits frames on a camera executor, while a JavaScript bridge call is
 * synchronous. The page therefore passes a callback name; the camera service
 * calls `deliver` when a JPEG frame has been captured and encoded. The host that
 * owns the requesting WebView installs the dispatcher. This mirrors the
 * existing NativeTtsRegistry pattern so the additive bridge contract stays
 * uniform across features.
 */
object CameraResultRegistry {

    fun interface Dispatcher {
        fun onResult(callbackName: String, json: String)
    }

    @Volatile
    private var dispatcher: Dispatcher? = null

    fun install(fn: Dispatcher?) { dispatcher = fn }

    /** [json] is a JSON object: { ok: Boolean, dataUri?: String, error?: String }. */
    fun deliver(callbackName: String, json: String) {
        val d = dispatcher ?: return
        try { d.onResult(callbackName, json) } catch (e: Exception) { /* never block the camera */ }
    }

    fun successJson(dataUri: String): String =
        "{\"ok\":true,\"dataUri\":\"" + jsonEscape(dataUri) + "\",\"mime\":\"image/jpeg\"}"

    fun errorJson(message: String): String =
        "{\"ok\":false,\"error\":\"" + jsonEscape(message) + "\"}"

    private fun jsonEscape(s: String): String = buildString {
        for (c in s) {
            when (c) {
                '\\' -> append("\\\\")
                '"' -> append("\\\"")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> append(c)
            }
        }
    }
}
