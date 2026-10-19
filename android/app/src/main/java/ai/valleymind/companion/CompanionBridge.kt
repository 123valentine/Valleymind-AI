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
}
