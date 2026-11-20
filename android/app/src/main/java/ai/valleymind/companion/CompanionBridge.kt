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

        /**
         * Enhanced native speech: reports real engine start/end plus a bounded
         * level envelope to the web companion for lip-sync. Hosts that do not
         * implement it keep the original completion-only behaviour.
         */
        fun speakNativeWithEvents(
            text: String,
            voiceId: String?,
            events: (event: String, value: String?) -> Unit
        ) {
            speakNative(text, voiceId) { ok -> events("end", ok.toString()) }
        }

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

        // ── camera "eyes" ────────────────────────────────────────────────
        // A user-started CameraX preview plus single-frame capture. Nothing is
        // uploaded by native code: the captured JPEG is handed back to the page,
        // which posts it to the SAME authenticated /api/vision/analyze endpoint
        // the rest of the app uses. Hosts with no camera keep the defaults below.
        fun cameraSupported(): Boolean = false
        fun cameraStart(facing: String?): Boolean = false
        fun cameraStop() { /* no-op by default */ }
        fun cameraSwitch(): Boolean = false
        fun cameraStateJson(): String = "{\"active\":false,\"facing\":\"back\"}"
        fun cameraCapture(callbackName: String?) {
            if (!callbackName.isNullOrBlank()) {
                CameraResultRegistry.deliver(
                    callbackName,
                    CameraResultRegistry.errorJson("camera-unsupported")
                )
            }
        }

        // ── compact overlay region (feature-detected via window.VMNative) ──
        fun setOverlayRegion(widthDp: Int, heightDp: Int): String =
            "{\"ok\":false,\"reason\":\"unsupported\"}"

        fun getOverlayRegion(): String = "{\"ok\":false,\"reason\":\"unsupported\"}"

        // ── optional screen context (navigation guidance) ─────────────
        fun screenContextSupported(): Boolean = false
        fun screenContextJson(): String = "{\"ok\":false,\"reason\":\"screen-context-unsupported\"}"
        fun screenContextSettings() { /* no-op by default */ }
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
     * Enhanced native TTS with real engine start/end events and a bounded level
     * envelope for lip-sync. Events are delivered as
     * window.__vmSpeechEvent_<callbackName>(event, value) via NativeTtsRegistry;
     * "end" also fires the original __vmTtsDone_ callback so both the old and
     * new web bridges keep working.
     */
    @JavascriptInterface
    fun ttsSpeakV2(text: String, voiceId: String?, callbackName: String?): Boolean {
        val clean = text.trim()
        if (clean.isEmpty()) return false
        val name = callbackName ?: return false
        host.speakNativeWithEvents(clean, voiceId) { event, value ->
            NativeTtsRegistry.deliverSpeech(name, event, value)
            if (event == "end") {
                NativeTtsRegistry.deliver(name, value?.toBooleanStrictOrNull() ?: false)
            }
        }
        return true
    }

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

    // ── camera eyes ───────────────────────────────────────────────────────
    @JavascriptInterface
    fun cameraSupported(): Boolean = host.cameraSupported()

    @JavascriptInterface
    fun cameraStart(facing: String?): Boolean = host.cameraStart(facing)

    @JavascriptInterface
    fun cameraStop() = host.cameraStop()

    @JavascriptInterface
    fun cameraSwitch(): Boolean = host.cameraSwitch()

    @JavascriptInterface
    fun cameraStateJson(): String = host.cameraStateJson()

    /**
     * Captures one frame. The JPEG data URI is delivered asynchronously as
     * window.__vmCameraResult_<callbackName>(json) so the page can post it to
     * the existing authenticated vision endpoint.
     */
    @JavascriptInterface
    fun cameraCapture(callbackName: String?) = host.cameraCapture(callbackName)

    // ── versioned contract (also surfaced as window.vmNative by the shim) ──
    @JavascriptInterface
    fun contractJson(): String = BridgeContract.contractJson(
        BuildConfig.VERSION_CODE, BuildConfig.VERSION_NAME, true
    )

    @JavascriptInterface
    fun capabilitiesJson(): String = BridgeContract.capabilitiesJson()

    @JavascriptInterface
    fun setOverlayRegion(widthDp: Int, heightDp: Int): String =
        host.setOverlayRegion(widthDp, heightDp)

    @JavascriptInterface
    fun getOverlayRegion(): String = host.getOverlayRegion()

    // ── optional screen context ─────────────────────────────────────────────
    /** True only when the user enabled the companion's AccessibilityService. */
    @JavascriptInterface
    fun screenContextSupported(): Boolean = host.screenContextSupported()

    /** A bounded JSON UI-tree snapshot, or a safe `ok:false` payload. */
    @JavascriptInterface
    fun screenContextJson(): String = host.screenContextJson()

    /** Opens Accessibility settings; never enables the service silently. */
    @JavascriptInterface
    fun screenContextSettings() = host.screenContextSettings()
}
