package ai.valleymind.companion

/**
 * NativeTtsRegistry — routes a native-TTS completion back to the right WebView.
 *
 * SystemTts finishes on the main thread and the bridge (ttsSpeak) is called on
 * the WebView's JavaBridge thread. The completion therefore cannot be returned
 * as the call's return value; the page passes a callback name and the host,
 * which owns the WebView, registers a dispatcher here. `deliver` runs the JS
 * callback `window.__vmTtsDone_<name>(ok)`.
 *
 * Kept deliberately tiny and process-wide: there is only ever one overlay
 * WebView and one main WebView, and a stale dispatcher is removed as soon as it
 * fires or is replaced.
 */
object NativeTtsRegistry {

    @Volatile
    private var dispatcher: ((String, Boolean) -> Unit)? = null

    /** The host installs this; it evaluates JS in its own WebView. */
    fun install(fn: ((String, Boolean) -> Unit)?) { dispatcher = fn }

    fun deliver(callbackName: String, ok: Boolean) {
        val d = dispatcher ?: return
        try { d(callbackName, ok) } catch (e: Exception) { /* one failure never blocks speech */ }
    }
}
