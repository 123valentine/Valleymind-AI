package ai.valleymind.companion

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

/**
 * MainActivity — the ValleyMind Android shell.
 *
 * The app UI is the EXISTING ValleyMind web app loaded in a WebView, so auth,
 * backend, companion engine and voice are all reused; nothing is re-implemented.
 * The only native addition is the system overlay (CompanionOverlayService),
 * driven through the CompanionBridge that the web layer already expects
 * (window.vmCompanionAndroid).
 *
 * Permission flow: the overlay cannot draw over other apps until the user grants
 * "Display over other apps". We never claim it is on before that; we deep-link
 * to the Android settings screen and RE-CHECK the real permission on return,
 * continuing the activation only if it was granted.
 */
class MainActivity : AppCompatActivity(), CompanionBridge.Host {

    private lateinit var webView: WebView
    private lateinit var bridge: CompanionBridge
    private var pendingAutoEnable = false

    private val overlayPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        // The system screen does not reliably return a result code, so ALWAYS
        // re-check the real permission rather than trusting the result.
        onOverlayPermissionReturned()
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* best-effort: the overlay works without notifications */ }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        val bridge = CompanionBridge(this)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            javaScriptCanOpenWindowsAutomatically = true
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        }
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)

        webView.addJavascriptInterface(bridge, "VmAndroidBridge")
        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                injectBridge(view)
            }
            override fun onPageFinished(view: WebView?, url: String?) {
                injectBridge(view)
                pushState()
            }
        }
        webView.loadUrl(VALLEY_BASE_URL)

        maybeRequestNotificationPermission()
    }

    private fun injectBridge(view: WebView?) {
        view?.evaluateJavascript(AndroidBridgeJs.ANDROID_BRIDGE_JS, null)
    }

    private fun pushState() {
        val json = overlayStateJson()
        webView.post { webView.evaluateJavascript("window.__vmAndroidState && window.__vmAndroidState($json);", null) }
    }

    // ── CompanionBridge.Host ────────────────────────────────────────

    override fun isOverlaySupported(): Boolean = true

    override fun hasOverlayPermission(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)

    override fun requestOverlayPermission() {
        pendingAutoEnable = true
        if (hasOverlayPermission()) { onOverlayPermissionReturned(); return }
        val intent = Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:$packageName")
        )
        overlayPermissionLauncher.launch(intent)
    }

    private fun onOverlayPermissionReturned() {
        if (hasOverlayPermission() && pendingAutoEnable) {
            pendingAutoEnable = false
            setOverlayVisible(true)
        } else if (!hasOverlayPermission()) {
            pendingAutoEnable = false
            pushState()
        }
    }

    override fun overlayStateJson(): String {
        val granted = hasOverlayPermission()
        val running = CompanionOverlayService.isRunning
        val visible = CompanionOverlayService.isVisible
        return "{\"granted\":$granted,\"running\":$running,\"visible\":$visible}"
    }

    override fun setOverlayVisible(on: Boolean) {
        if (on && !hasOverlayPermission()) {
            requestOverlayPermission()
            return
        }
        if (on) CompanionOverlayService.start(this, VALLEY_BASE_URL)
        else CompanionOverlayService.stop(this)
        // Reflect the ACTUAL service/overlay state back to the page shortly after.
        webView.postDelayed({ pushState() }, 350)
    }

    override fun onWebBridgeAttached() {
        pushState()
    }

    override fun onResume() {
        super.onResume()
        // Permission may have changed in system settings while we were away.
        if (hasOverlayPermission() && pendingAutoEnable) {
            pendingAutoEnable = false
            setOverlayVisible(true)
        }
        pushState()
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    @Deprecated("Back should keep the floating companion alive when it is on screen.")
    override fun onBackPressed() {
        if (CompanionOverlayService.isVisible) {
            moveTaskToBack(true)
        } else {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    companion object {
        // The ValleyMind deployment the WebView loads. Use http://10.0.2.2:5000
        // to reach a backend running on the host machine from the emulator.
        val VALLEY_BASE_URL: String = BuildConfig.VALLEY_BASE_URL
    }
}
