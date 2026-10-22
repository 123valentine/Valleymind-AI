package ai.valleymind.companion

import android.webkit.JavascriptInterface

/**
 * CompanionBridge — the ONLY channel the web page uses to drive the Android
 * floating overlay.
 *
 * It mirrors the contract the existing web layer already expects
 * (window.vmCompanionAndroid): setVisible / getVisible / hasPermission /
 * requestPermission — and it always reports ACTUAL state, never the mere
 * request. The JavaScript shim installed by AndroidBridgeJs wraps these
 * synchronous methods in the Promise-returning API static/settings.js calls.
 *
 * Nothing here creates a second brain or conversation: the overlay WebView
 * talks to the same ValleyMind backend endpoints as the app.
 */
class CompanionBridge(private val host: Host) {

    interface Host {
        fun isOverlaySupported(): Boolean
        fun hasOverlayPermission(): Boolean
        fun requestOverlayPermission()
        fun overlayStateJson(): String
        fun setOverlayVisible(on: Boolean)
        fun onWebBridgeAttached()
        /**
         * Speak [text] through the native TTS engine. [done] must be invoked
         * exactly once when the utterance ends (or fails). Implementations that
         * have no engine should call done(false) immediately.
         */
        fun speakNative(text: String, voiceId: String?, done: (Boolean) -> Unit) { done(false) }
        fun stopNativeSpeech() { /* no-op by default */ }

        // ── native speech recognition ───────────────────────────────────
        // Android's WebView has no Web Speech API, so the overlay page cannot
        // recognise speech in-page. These let the page drive the platform's own
        // SpeechRecognizer instead of uploading microphone audio to the
        // server. Hosts without a recogniser report "not supported", and the
        // page then falls back to its existing recording path.
        fun asrSupported(): Boolean = false
        fun asrStart(locale: String?): Boolean = false
        fun asrStop() { /* no-op by default */ }
        fun asrCancel() { /* no-op by default */ }
    }

    @JavascriptInterface
    fun isOverlaySupported(): Boolean = host.isOverlaySupported()

    @JavascriptInterface
    fun hasPermission(): Boolean = host.hasOverlayPermission()

    @JavascriptInterface
    fun requestPermission() = host.requestOverlayPermission()

    @JavascriptInterface
    fun getStateJson(): String = host.overlayStateJson()

    @JavascriptInterface
    fun setVisible(on: Boolean) = host.setOverlayVisible(on)

    @JavascriptInterface
    fun notifyBridgeReady() = host.onWebBridgeAttached()

    /**
     * Native TTS entry point. Speech runs on the main thread and completion is
     * delivered back to the WebView through NativeTtsRegistry, which the host
     * installs (it owns the WebView). Returns false if the engine could not even
     * start the utterance.
     */
    @JavascriptInterface
    fun ttsSpeak(text: String, voiceId: String?, callbackName: String?): Boolean {
        val clean = text.trim()
        if (clean.isEmpty()) return false
        host.speakNative(clean, voiceId) { ok ->
            if (!callbackName.isNullOrBlank()) {
                NativeTtsRegistry.deliver(callbackName, ok)
            }
        }
        return true
    }

    @JavascriptInterface
    fun ttsStop() = host.stopNativeSpeech()

    /**
     * Native speech-recognition entry points. The recogniser lives in the host
     * (the overlay service owns the WebView and the context); the page only
     * asks it to start and stop, and receives the results as
     * window.__vmAsrEvent(json).
     */
    @JavascriptInterface
    fun asrSupported(): Boolean = host.asrSupported()

    @JavascriptInterface
    fun asrStart(locale: String?): Boolean = host.asrStart(locale)

    @JavascriptInterface
    fun asrStop() = host.asrStop()

    @JavascriptInterface
    fun asrCancel() = host.asrCancel()
}
