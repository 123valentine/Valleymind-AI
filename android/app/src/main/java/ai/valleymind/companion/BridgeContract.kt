package ai.valleymind.companion

/**
 * The versioned native-bridge contract.
 *
 * Compatibility is ADDITIVE ONLY: existing native method names, shim method
 * names, event names and overlay page markers must never be renamed or removed,
 * because the backend serves the web bundle independently of the APK and older
 * shells keep working with newer pages (and vice versa). New behaviour is
 * advertised through [CAPABILITIES]; callers must feature-detect instead of
 * assuming the shell version.
 *
 * The matching machine-readable manifest lives at android/bridge-contract.json
 * and is checked against these constants by the test suite.
 */
object BridgeContract {

    /** Bumped only for additive changes; 1 means "legacy shell without a contract". */
    const val VERSION = 2

    val NATIVE_METHODS = listOf(
        "isOverlaySupported",
        "hasPermission",
        "requestPermission",
        "getStateJson",
        "setVisible",
        "notifyBridgeReady",
        "ttsSpeak",
        "ttsSpeakV2",
        "ttsStop",
        "cameraSupported",
        "cameraStart",
        "cameraStop",
        "cameraSwitch",
        "cameraStateJson",
        "cameraCapture",
        "asrSupported",
        "asrStart",
        "asrStop",
        "asrCancel",
        "contractJson",
        "capabilitiesJson",
        "setOverlayRegion",
        "getOverlayRegion",
        "screenContextSupported",
        "screenContextJson",
        "screenContextSettings"
    )

    /** Methods the injected shim (AndroidBridgeJs) promises to the web layer. */
    val SHIM_METHODS = listOf(
        "isSupported",
        "hasPermission",
        "requestPermission",
        "getState",
        "getVisible",
        "setVisible",
        "onCompanionVisibility",
        "hasNativeTts",
        "ttsSpeak",
        "ttsStop",
        "hasNativeTtsV2",
        "ttsSpeakV2",
        "onSpeechEvent",
        "hasNativeCamera",
        "cameraStart",
        "cameraStop",
        "cameraSwitch",
        "cameraState",
        "cameraCapture",
        "hasNativeAsr",
        "asrStart",
        "asrStop",
        "asrCancel",
        "onAsrEvent",
        "screenContextSupported",
        "screenContextJson",
        "screenContextSettings"
    )

    val CAPABILITIES = listOf(
        "overlay", "overlayRegion", "tts", "ttsEvents", "camera", "asr", "files",
        "liveCall", "dragPersistence", "screenContext"
    )

    val EVENTS = listOf(
        "__vmAndroidState", "__vmAsrEvent", "__vmTtsDone_",
        "__vmSpeechEvent_", "__vmCameraResult_", "__vmCameraState"
    )

    val OVERLAY_MARKERS = listOf("vmOverlayRoot", "stage", "btnHide")

    fun capabilitiesJson(): String = jsonArray(CAPABILITIES)

    fun contractJson(versionCode: Int, versionName: String, ready: Boolean): String =
        buildString {
            append("{\"version\":").append(VERSION)
            append(",\"ready\":").append(ready)
            append(",\"shell\":{\"versionCode\":").append(versionCode)
            append(",\"versionName\":\"").append(escape(versionName)).append("\"}")
            append(",\"capabilities\":").append(capabilitiesJson())
            append('}')
        }

    /** Deliberately uninformative payload for pages outside the trusted origin. */
    fun untrustedJson(): String = "{\"version\":0,\"ready\":false,\"capabilities\":[]}"

    fun supportedMethodsJson(): String = jsonArray(NATIVE_METHODS)

    private fun jsonArray(values: List<String>): String =
        values.joinToString(prefix = "[", postfix = "]", separator = ",") { "\"" + escape(it) + "\"" }

    private fun escape(raw: String): String {
        val sb = StringBuilder(raw.length + 8)
        for (ch in raw) {
            when {
                ch == '\\' -> sb.append("\\\\")
                ch == '"' -> sb.append("\\\"")
                ch == '\n' -> sb.append("\\n")
                ch == '\r' -> sb.append("\\r")
                ch == '\t' -> sb.append("\\t")
                ch < ' ' -> sb.append(String.format(java.util.Locale.ROOT, "\\u%04x", ch.code))
                else -> sb.append(ch)
            }
        }
        return sb.toString()
    }
}
