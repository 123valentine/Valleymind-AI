package ai.valleymind.companion

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

/**
 * MainActivity — the ValleyMind Android shell.
 *
 * The app UI is the EXISTING ValleyMind web app loaded in a WebView, so auth,
 * backend, companion engine and voice are all reused; nothing is re-implemented.
 * The only native additions are the system overlay (CompanionOverlayService),
 * driven through the CompanionBridge that the web layer already expects
 * (window.vmCompanionAndroid), and a native startup-failure screen.
 *
 * Startup: a WebView has no load timeout of its own. When the backend is cold
 * or unreachable the page never finishes, which left the app stranded on a blank
 * screen forever. MainActivity now runs a LoadMonitor: if content has not
 * committed within LoadMonitor.DEFAULT_TIMEOUT_MS — or the load fails with a
 * network, HTTP, SSL or renderer error — a native error screen with a Retry is
 * shown. The backend is never reported as ready unless it actually served the
 * page.
 *
 * Permission flow: the overlay cannot draw over other apps until the user grants
 * "Display over other apps". We never claim it is on before that; we deep-link
 * to the Android settings screen and RE-CHECK the real permission on return,
 * continuing the activation only if it was granted.
 */
class MainActivity : AppCompatActivity(), CompanionBridge.Host {

    private lateinit var webView: WebView
    private lateinit var bridge: CompanionBridge
    private var rendererGone = false
    private lateinit var configStore: AppConfigStore
    private lateinit var errorView: View
    private lateinit var errorTitle: TextView
    private lateinit var errorMessage: TextView
    private lateinit var errorServer: TextView

    private var pendingAutoEnable = false

    /** The origin the WebView loads; production default, runtime-overridable. */
    private var baseUrl: String = BuildConfig.VALLEY_BASE_URL

    private val loadMonitor = LoadMonitor(LoadMonitor.DEFAULT_TIMEOUT_MS)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val loadTimeoutRunnable = Runnable {
        if (loadMonitor.onTimeoutFired()) showErrorForReason(LoadMonitor.REASON_TIMEOUT)
    }

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

        configStore = AppConfigStore(this)
        baseUrl = configStore.getBaseUrl(BuildConfig.VALLEY_BASE_URL)

        webView = findViewById(R.id.webView)
        errorView = findViewById(R.id.errorView)
        errorTitle = findViewById(R.id.errorTitle)
        errorMessage = findViewById(R.id.errorMessage)
        errorServer = findViewById(R.id.errorServer)
        bridge = CompanionBridge(this)

        findViewById<Button>(R.id.btnRetry).setOnClickListener { loadApp() }
        findViewById<Button>(R.id.btnChangeServer).setOnClickListener { showBaseUrlDialog() }

        setupWebView()

        loadApp()
        maybeRequestNotificationPermission()
    }

    /** Creates and wires the WebView. Called again after a renderer crash. */
    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
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
            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                injectBridge(view)
            }

            // Only fires when real content started rendering, so it is the
            // trustworthy "the backend actually served the app" signal.
            override fun onPageCommitVisible(view: WebView?, url: String?) {
                loadMonitor.onCommitted()
                cancelLoadTimeout()
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                injectBridge(view)
                pushState()
                if (loadMonitor.state == LoadMonitor.State.LOADING) {
                    // Finished without committing and without a reported error:
                    // treat as failed so the user is never left on a blank screen.
                    loadMonitor.onFailed(LoadMonitor.REASON_UNCOMMITTED)
                    showErrorForReason(LoadMonitor.REASON_UNCOMMITTED)
                }
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame != true) return
                loadMonitor.onFailed(LoadMonitor.REASON_NETWORK)
                showErrorForReason(LoadMonitor.REASON_NETWORK)
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?
            ) {
                if (request?.isForMainFrame != true) return
                val status = errorResponse?.statusCode ?: 0
                if (status < 400) return
                loadMonitor.onFailed(LoadMonitor.REASON_HTTP)
                showErrorForReason(LoadMonitor.REASON_HTTP, status)
            }

            override fun onReceivedSslError(
                view: WebView?,
                handler: SslErrorHandler?,
                error: android.net.http.SslError?
            ) {
                handler?.cancel()
                loadMonitor.onFailed(LoadMonitor.REASON_SSL)
                showErrorForReason(LoadMonitor.REASON_SSL)
            }

            override fun onRenderProcessGone(
                view: WebView?,
                detail: RenderProcessGoneDetail?
            ): Boolean {
                // The renderer is dead: rebuild the WebView before the next
                // load so Retry never calls loadUrl() on a destroyed view.
                rendererGone = true
                loadMonitor.onFailed(LoadMonitor.REASON_RENDERER_GONE)
                showErrorForReason(LoadMonitor.REASON_RENDERER_GONE)
                return true
            }
        }
    }

    /** Destroys the dead WebView and builds a fresh one in its place. */
    private fun recreateWebView() {
        rendererGone = false
        val old = webView
        val parent = old.parent as? android.view.ViewGroup
        val index = parent?.indexOfChild(old) ?: -1
        val lp = old.layoutParams
        try { old.destroy() } catch (e: Exception) { /* ignore */ }
        parent?.removeView(old)
        val fresh = WebView(this)
        webView = fresh
        if (parent != null && index >= 0) parent.addView(fresh, index, lp)
        else parent?.addView(fresh)
        setupWebView()
    }

    private fun loadApp() {
        if (rendererGone) recreateWebView()
        hideError()
        loadMonitor.onLoadStarted()
        cancelLoadTimeout()
        mainHandler.postDelayed(loadTimeoutRunnable, loadMonitor.timeoutMs)
        webView.loadUrl(baseUrl)
    }

    private fun cancelLoadTimeout() {
        mainHandler.removeCallbacks(loadTimeoutRunnable)
    }

    private fun showErrorForReason(reason: String?, httpStatus: Int = 0) {
        cancelLoadTimeout()
        val message = when (reason) {
            LoadMonitor.REASON_TIMEOUT -> getString(R.string.load_error_message_timeout)
            LoadMonitor.REASON_HTTP ->
                getString(R.string.load_error_message_http, httpStatus.coerceAtLeast(0))
            LoadMonitor.REASON_SSL -> getString(R.string.load_error_message_ssl)
            LoadMonitor.REASON_RENDERER_GONE -> getString(R.string.load_error_message_renderer)
            else -> getString(R.string.load_error_message_network)
        }
        errorTitle.text = getString(R.string.load_error_title)
        errorMessage.text = message
        errorServer.text = getString(R.string.load_error_server_label, baseUrl)
        webView.visibility = View.GONE
        errorView.visibility = View.VISIBLE
    }

    private fun hideError() {
        errorView.visibility = View.GONE
        webView.visibility = View.VISIBLE
    }

    // ── runtime base URL (local backend testing) ────────────────────

    private fun showBaseUrlDialog() {
        val input = EditText(this).apply {
            hint = getString(R.string.base_url_dialog_hint)
            setText(baseUrl)
            setSelection(text.length)
            inputType = InputType.TYPE_TEXT_VARIATION_URI
        }
        val pad = (16 * resources.displayMetrics.density).toInt()
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
            addView(TextView(this@MainActivity).apply {
                text = getString(R.string.base_url_dialog_message)
            })
            addView(input, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = pad / 2 })
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.base_url_dialog_title)
            .setView(container)
            .setPositiveButton(R.string.base_url_dialog_save) { _, _ ->
                applyBaseUrl(input.text.toString())
            }
            .setNeutralButton(R.string.base_url_dialog_reset) { _, _ ->
                applyBaseUrl(BuildConfig.VALLEY_BASE_URL)
            }
            .setNegativeButton(R.string.base_url_dialog_cancel, null)
            .show()
    }

    private fun applyBaseUrl(raw: String) {
        val normalized = BaseUrlConfig.normalize(raw)
        if (normalized == null) {
            Toast.makeText(this, R.string.base_url_dialog_invalid, Toast.LENGTH_SHORT).show()
            return
        }
        if (normalized == BuildConfig.VALLEY_BASE_URL) configStore.clearBaseUrlOverride()
        else configStore.setBaseUrlOverride(normalized)
        baseUrl = configStore.getBaseUrl(BuildConfig.VALLEY_BASE_URL)
        // Keep the floating companion on the same origin as the main app.
        if (CompanionOverlayService.isRunning) {
            CompanionOverlayService.start(this, baseUrl)
        }
        loadApp()
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
        // The decision is the tested one (OverlayStateMachine), not a re-implementation:
        // continue the activation only if the permission was ACTUALLY granted.
        when (OverlayStateMachine.onPermissionReturned(hasOverlayPermission(), pendingAutoEnable)) {
            OverlayStateMachine.Action.START -> {
                pendingAutoEnable = false
                setOverlayVisible(true)
            }
            else -> {
                pendingAutoEnable = false
                pushState()
            }
        }
    }

    override fun overlayStateJson(): String {
        val granted = hasOverlayPermission()
        val running = CompanionOverlayService.isRunning
        val visible = CompanionOverlayService.isVisible
        return "{\"granted\":$granted,\"running\":$running,\"visible\":$visible}"
    }

    override fun setOverlayVisible(on: Boolean) {
        val action = OverlayStateMachine.onToggle(on, hasOverlayPermission())
        when (action) {
            OverlayStateMachine.Action.REQUEST_PERMISSION -> requestOverlayPermission()
            OverlayStateMachine.Action.START -> CompanionOverlayService.start(this, baseUrl)
            OverlayStateMachine.Action.STOP -> CompanionOverlayService.stop(this)
            else -> { /* no-op */ }
        }
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

    override fun onDestroy() {
        cancelLoadTimeout()
        super.onDestroy()
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
        // The build-time ValleyMind deployment. It can be overridden at runtime
        // (Change server / AppConfigStore) to point at a local backend, e.g.
        // http://10.0.2.2:8000 from the emulator or http://<lan-ip>:8000 on a
        // real device, without rebuilding.
        val VALLEY_BASE_URL: String = BuildConfig.VALLEY_BASE_URL
    }
}
