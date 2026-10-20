package ai.valleymind.companion

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.graphics.PixelFormat
import android.net.http.SslError
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlin.math.roundToInt

/**
 * CompanionOverlayService — the native, system-wide floating companion.
 *
 * A browser/PWA cannot draw over other apps; this is the part Android requires a
 * native app for. It owns ONE WindowManager overlay window
 * (TYPE_APPLICATION_OVERLAY) whose content is a WebView loading the SAME
 * companion surface as the desktop app (/static/companion_overlay.html). That
 * page talks to the SAME ValleyMind backend and session cookie, so there is
 * exactly one brain and one conversation — not a second companion product.
 *
 * FAIL-CLOSED SCOPE: the backend serves a catch-all that falls back to
 * index.html for unmatched paths. If the companion page were missing from a
 * deployment, that fallback would put the ENTIRE ValleyMind app inside the
 * floating window. This service therefore (1) loads exactly one URL, (2) blocks
 * every other top-level navigation, (3) verifies the loaded document really is
 * the companion page, and (4) gives up with a small native error panel (Retry /
 * Hide) instead of ever showing the app. See OverlayPageVerifier.
 *
 * Lifecycle:
 *   ACTION_SHOW -> startForeground + attach the overlay window
 *   ACTION_HIDE -> detach the window, release the WebView, stop foreground
 *
 * The overlay is NOT focusable and NOT modal, so touches outside the character
 * pass through to whatever app is underneath; only the drag handle and the
 * character's own controls capture input.
 */
class CompanionOverlayService : Service() {

    private var windowManager: WindowManager? = null
    private var rootView: View? = null
    private var webView: WebView? = null
    private var errorPanel: View? = null
    private var params: WindowManager.LayoutParams? = null
    private var baseUrl: String = DEFAULT_BASE_URL

    /** The companion URL this service last asked the WebView to load. */
    private var requestedUrl: String? = null

    private val overlayLoadMonitor = LoadMonitor(OVERLAY_TIMEOUT_MS)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val overlayTimeoutRunnable = Runnable {
        if (overlayLoadMonitor.onTimeoutFired()) showOverlayError("timed out")
    }

    private val overlayHost = object : CompanionBridge.Host {
        override fun isOverlaySupported(): Boolean = true
        override fun hasOverlayPermission(): Boolean = canDrawOverlays()
        override fun requestOverlayPermission() { /* granted in the main app */ }
        override fun overlayStateJson(): String = stateJson()
        override fun setOverlayVisible(on: Boolean) {
            val action = OverlayStateMachine.onToggle(on, canDrawOverlays())
            when (action) {
                OverlayStateMachine.Action.START ->
                    CompanionOverlayService.start(this@CompanionOverlayService, baseUrl)
                OverlayStateMachine.Action.STOP ->
                    CompanionOverlayService.stop(this@CompanionOverlayService)
                else -> { /* no-op */ }
            }
        }
        override fun onWebBridgeAttached() = pushStateToOverlay()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_HIDE -> {
                hideOverlay()
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                intent?.getStringExtra(EXTRA_BASE_URL)?.let { if (it.isNotBlank()) baseUrl = it }
                startInForeground()
                if (canDrawOverlays()) {
                    showOverlay()
                } else {
                    isVisible = false
                    isRunning = false
                    stopSelf()
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        cancelOverlayTimeout()
        detachOverlay()
        super.onDestroy()
    }

    // ── overlay window ──────────────────────────────────────────────

    private fun showOverlay() {
        if (rootView != null) {
            isVisible = true
            isRunning = true
            // The base URL can change while the overlay is already up (Change
            // server). Without this the floating window would keep rendering the
            // previous origin, so the companion and the main app would point at
            // different backends. The currently loaded document decides.
            // WebView.url can still be null for a moment after loadUrl(), so
            // fall back to the URL this service itself requested.
            val loaded = webView?.url ?: requestedUrl
            if (OverlayPageVerifier.mustReloadForBaseChange(loaded, baseUrl)) {
                loadCompanionPage()
            }
            return
        }
        val density = resources.displayMetrics.density
        val view = buildOverlayView(density)

        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else
            @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE

        val lp = WindowManager.LayoutParams(
            (210 * density).roundToInt(),
            (300 * density).roundToInt(),
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = (resources.displayMetrics.widthPixels * 0.02f).roundToInt()
            y = (resources.displayMetrics.heightPixels * 0.12f).roundToInt()
        }

        rootView = view
        params = lp
        windowManager?.addView(view, lp)
        isVisible = true
        isRunning = true
        loadCompanionPage()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun buildOverlayView(density: Float): View {
        val wv = WebView(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        }
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        wv.addJavascriptInterface(CompanionBridge(overlayHost), "VmAndroidBridge")
        wv.webViewClient = object : WebViewClient() {

            // Hard scope guard: nothing but the companion page may ever be the
            // overlay's document. Anything else (including the app's index.html
            // catch-all fallback) is blocked and surfaced as an error.
            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?
            ): Boolean {
                val target = request?.url?.toString()
                if (!OverlayPageVerifier.shouldAllowNavigation(target, baseUrl)) {
                    showOverlayError("navigation blocked: $target")
                    return true
                }
                return false
            }

            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                view?.evaluateJavascript(AndroidBridgeJs.ANDROID_BRIDGE_JS, null)
            }

            override fun onPageCommitVisible(view: WebView?, url: String?) {
                overlayLoadMonitor.onCommitted()
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                view?.evaluateJavascript(AndroidBridgeJs.ANDROID_BRIDGE_JS, null)
                pushStateToOverlay()
                verifyCompanionDocument()
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame != true) return
                showOverlayError("network error")
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?
            ) {
                if (request?.isForMainFrame != true) return
                val status = errorResponse?.statusCode ?: 0
                if (status < 400) return
                showOverlayError("HTTP $status")
            }

            override fun onReceivedSslError(
                view: WebView?,
                handler: SslErrorHandler?,
                error: SslError?
            ) {
                handler?.cancel()
                showOverlayError("ssl error")
            }

            override fun onRenderProcessGone(
                view: WebView?,
                detail: RenderProcessGoneDetail?
            ): Boolean {
                showOverlayError("renderer gone")
                return true
            }
        }
        webView = wv

        val error = buildOverlayErrorView(density)
        errorPanel = error

        // A slim drag handle; the character area below stays interactive.
        val drag = View(this).apply { setBackgroundColor(0x22000000) }
        wireDrag(drag)

        val content = FrameLayout(this).apply {
            addView(wv, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            ))
            addView(error, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            ).apply { gravity = Gravity.CENTER })
        }

        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(drag, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, (26 * density).roundToInt()))
            addView(content, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        }
    }

    /** Loads the ONE allowed URL, or fails closed if it cannot be built. */
    private fun loadCompanionPage() {
        val target = OverlayPageVerifier.companionUrl(baseUrl)
        if (target == null) {
            showOverlayError("invalid base URL")
            return
        }
        hideOverlayError()
        overlayLoadMonitor.onLoadStarted()
        cancelOverlayTimeout()
        mainHandler.postDelayed(overlayTimeoutRunnable, overlayLoadMonitor.timeoutMs)
        requestedUrl = target
        webView?.loadUrl(target)
    }

    /**
     * Content verification: the loaded document must carry the companion page's
     * own markers. The index.html catch-all fallback has none of them, so a
     * missing/stale companion page can never render the full app here.
     */
    private fun verifyCompanionDocument() {
        val wv = webView ?: return
        wv.evaluateJavascript(OverlayPageVerifier.PAGE_MARKER_JS) { result ->
            if (OverlayPageVerifier.looksLikeCompanionDocument(result == "true")) {
                cancelOverlayTimeout()
            } else {
                showOverlayError("document is not the companion page")
            }
        }
    }

    private fun showOverlayError(reason: String) {
        cancelOverlayTimeout()
        overlayLoadMonitor.onFailed(reason)
        webView?.visibility = View.GONE
        errorPanel?.visibility = View.VISIBLE
    }

    private fun hideOverlayError() {
        errorPanel?.visibility = View.GONE
        webView?.visibility = View.VISIBLE
    }

    private fun cancelOverlayTimeout() {
        mainHandler.removeCallbacks(overlayTimeoutRunnable)
    }

    private fun buildOverlayErrorView(density: Float): View {
        val pad = (12 * density).roundToInt()
        val title = TextView(this).apply {
            text = getString(R.string.overlay_error_title)
            setTextColor(0xFFE2E8F0.toInt())
            textSize = 13f
            gravity = Gravity.CENTER
        }
        val message = TextView(this).apply {
            text = getString(R.string.overlay_error_message)
            setTextColor(0xFF94A3B8.toInt())
            textSize = 11f
            gravity = Gravity.CENTER
            setPadding(0, (6 * density).roundToInt(), 0, 0)
        }
        val retry = Button(this).apply {
            text = getString(R.string.overlay_error_retry)
            textSize = 12f
            setOnClickListener { loadCompanionPage() }
        }
        val hide = Button(this).apply {
            text = getString(R.string.overlay_error_hide)
            textSize = 12f
            setOnClickListener { CompanionOverlayService.stop(this@CompanionOverlayService) }
        }
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(0xF20B1220.toInt())
            setPadding(pad, pad, pad, pad)
            addView(title)
            addView(message)
            addView(retry, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = pad })
            addView(hide, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = (4 * density).roundToInt() })
        }
    }

    private fun wireDrag(handle: View) {
        var startX = 0
        var startY = 0
        var touchX = 0f
        var touchY = 0f
        handle.setOnTouchListener { _, event ->
            val lp = params ?: return@setOnTouchListener false
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    startX = lp.x; startY = lp.y
                    touchX = event.rawX; touchY = event.rawY
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    lp.x = startX + (event.rawX - touchX).roundToInt()
                    lp.y = startY + (event.rawY - touchY).roundToInt()
                    rootView?.let { windowManager?.updateViewLayout(it, lp) }
                    true
                }
                else -> false
            }
        }
    }

    private fun detachOverlay() {
        rootView?.let { try { windowManager?.removeView(it) } catch (e: Exception) { /* already gone */ } }
        webView?.let {
            try { it.removeJavascriptInterface("VmAndroidBridge") } catch (e: Exception) { /* ignore */ }
            try { it.destroy() } catch (e: Exception) { /* ignore */ }
        }
        rootView = null
        webView = null
        errorPanel = null
        params = null
        requestedUrl = null
        isVisible = false
        isRunning = false
    }

    private fun hideOverlay() {
        detachOverlay()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
    }

    private fun pushStateToOverlay() {
        val json = stateJson()
        webView?.post { webView?.evaluateJavascript("window.__vmAndroidState && window.__vmAndroidState($json);", null) }
    }

    private fun canDrawOverlays(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)

    private fun stateJson(): String =
        "{\"granted\":${canDrawOverlays()},\"running\":$isRunning,\"visible\":$isVisible}"

    // ── foreground notification ─────────────────────────────────────

    private fun startInForeground() {
        ServiceCompat.startForeground(
            this,
            NOTIF_ID,
            buildNotification(),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE)
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            else
                0
        )
    }

    private fun buildNotification(): android.app.Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.overlay_notification_title))
            .setContentText(getString(R.string.overlay_notification_text))
            .setSmallIcon(R.drawable.ic_stat_companion)
            .setOngoing(true)
            .setContentIntent(open)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val mgr = getSystemService(NotificationManager::class.java)
        if (mgr.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.overlay_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply { description = getString(R.string.overlay_channel_desc) }
        mgr.createNotificationChannel(channel)
    }

    companion object {
        const val ACTION_SHOW = "ai.valleymind.companion.action.SHOW"
        const val ACTION_HIDE = "ai.valleymind.companion.action.HIDE"
        const val EXTRA_BASE_URL = "base_url"
        const val DEFAULT_BASE_URL = "https://valleymind-ai.onrender.com"
        private const val CHANNEL_ID = "vm_companion_overlay"
        private const val NOTIF_ID = 7317

        /** Overlay startup deadline; shorter than the main app's, with Retry. */
        private const val OVERLAY_TIMEOUT_MS = 20_000L

        // Process-wide REAL state, read by MainActivity to answer the web bridge.
        @Volatile var isVisible: Boolean = false
            private set
        @Volatile var isRunning: Boolean = false
            private set

        fun start(context: Context, baseUrl: String) {
            val intent = Intent(context, CompanionOverlayService::class.java)
                .setAction(ACTION_SHOW)
                .putExtra(EXTRA_BASE_URL, baseUrl)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, CompanionOverlayService::class.java).setAction(ACTION_HIDE)
            try {
                context.startService(intent)
            } catch (e: Exception) {
                context.stopService(Intent(context, CompanionOverlayService::class.java))
            }
        }
    }
}
