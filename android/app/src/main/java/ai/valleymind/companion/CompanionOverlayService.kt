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
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.webkit.PermissionRequest
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
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlin.math.max
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
 * pass through to whatever app is underneath; only the drag surface and the
 * character's own controls capture input.
 *
 * DRAGGING: the whole window is the drag surface (OverlayDragLayout) — there is
 * no separate handle strip to hunt for. A gesture that stays inside the
 * platform touch slop is a TAP and reaches the page (that is what wakes the
 * companion); the moment it exceeds the slop the layout takes the gesture over
 * and the WebView receives ACTION_CANCEL, so a drag can never also fire a tap.
 * Every position is clamped to the usable screen area, so the companion can no
 * longer be dragged out of reach and lost.
 */
class CompanionOverlayService : Service() {

    private var windowManager: WindowManager? = null
    private var rootView: View? = null
    private var webView: WebView? = null
    private var errorPanel: View? = null
    private var params: WindowManager.LayoutParams? = null
    private var baseUrl: String = DEFAULT_BASE_URL

    /** The drag surface: the whole window moves the character. */
    private var dragLayout: OverlayDragLayout? = null

    /** Platform speech recogniser (Android's WebView has no Web Speech API). */
    private var asr: CompanionAsr? = null

    /** Native TTS fallback for the overlay page (no speechSynthesis in WebView). */
    private var systemTts: SystemTts? = null

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
        override fun speakNative(text: String, voiceId: String?, done: (Boolean) -> Unit) {
            systemTts?.speak(text, voiceId, done) ?: done(false)
        }
        override fun stopNativeSpeech() {
            systemTts?.stop()
        }
        override fun asrSupported(): Boolean = asr?.supported == true
        override fun asrStart(locale: String?): Boolean {
            val engine = asr ?: return false
            return engine.start(locale ?: "en-US")
        }
        override fun asrStop() {
            asr?.stop()
        }
        override fun asrCancel() {
            asr?.cancel()
        }
    }

    /** Moves the overlay window while the user drags the character. */
    private val dragHost = object : OverlayDragLayout.Host {
        override fun onDragStart() { /* the page keeps rendering while dragging */ }

        override fun onDrag(x: Int, y: Int) {
            val lp = params ?: return
            val view = rootView ?: return
            if (lp.x == x && lp.y == y) return
            lp.x = x
            lp.y = y
            dragLayout?.let {
                it.windowX = x
                it.windowY = y
            }
            try {
                windowManager?.updateViewLayout(view, lp)
            } catch (e: Exception) { /* the window was removed mid-gesture */ }
        }

        override fun onDragEnd() { /* nothing to settle: the window is already where it belongs */ }
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
        // The drag surface measures from the window's real position.
        dragLayout?.let {
            it.windowX = lp.x
            it.windowY = lp.y
        }
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
        if (systemTts == null) {
            systemTts = SystemTts(this).also { it.init() }
        }
        // The overlay page speaks through native TTS; its completion is evaluated
        // back into this WebView.
        NativeTtsRegistry.install { name, ok ->
            val js = "window.__vmTtsDone_" + name + " && window.__vmTtsDone_" + name + "(" + ok + ");"
            wv.post { wv.evaluateJavascript(js, null) }
        }
        wv.addJavascriptInterface(CompanionBridge(overlayHost), "VmAndroidBridge")
        // Tap-to-talk needs the microphone. Without a WebChromeClient,
        // getUserMedia / recognition are refused inside the overlay WebView.
        wv.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest?) {
                if (request == null) return
                val wantsAudio = request.resources?.any {
                    it == PermissionRequest.RESOURCE_AUDIO_CAPTURE
                } == true
                val granted = ContextCompat.checkSelfPermission(
                    this@CompanionOverlayService, android.Manifest.permission.RECORD_AUDIO
                ) == android.content.pm.PackageManager.PERMISSION_GRANTED
                if (wantsAudio && granted) request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
                else request.deny()
            }
        }
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

        // The platform recogniser: Android's WebView has no Web Speech API, so
        // the page drives SpeechRecognizer through the bridge and receives the
        // transcript as window.__vmAsrEvent(json).
        if (asr == null) {
            asr = CompanionAsr(this).also { engine ->
                engine.setListener(object : CompanionAsr.Listener {
                    override fun onAsrReady() = deliverAsrEvent(asrEvent("ready"))
                    override fun onAsrPartial(text: String) = deliverAsrEvent(asrEvent("partial", text))
                    override fun onAsrEndOfSpeech() = deliverAsrEvent(asrEvent("end"))
                    override fun onAsrFinal(text: String) = deliverAsrEvent(asrEvent("final", text))
                    override fun onAsrError(reason: String) = deliverAsrEvent(asrEvent("error", null, reason))
                })
            }
        }

        val error = buildOverlayErrorView(density)
        errorPanel = error

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

        // The whole window is the drag surface: no handle strip, no visible
        // chrome, and the character itself is what the user grabs.
        val layout = OverlayDragLayout(this).apply {
            host = dragHost
            windowX = params?.x ?: 0
            windowY = params?.y ?: 0
            boundsProvider = { overlayBounds() }
            addView(content, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.MATCH_PARENT
            ))
        }
        dragLayout = layout
        return layout
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

    /**
     * The rectangle the floating window may occupy.
     *
     * The window keeps its full size, but its TOP-LEFT position is clamped so
     * the companion always stays completely on screen: with the old un-clamped
     * drag (and FLAG_LAYOUT_NO_LIMITS) the character could be dragged past an
     * edge and become unreachable — the classic "I lost my overlay" bug.
     * System bars and display cutouts are honoured where the platform reports
     * them; the clamp is always valid, so a narrow screen can never produce an
     * empty range.
     */
    private fun overlayBounds(): OverlayBounds {
        val dm = resources.displayMetrics
        val lp = params
        val w = lp?.width?.takeIf { it > 0 } ?: (210 * dm.density).roundToInt()
        val h = lp?.height?.takeIf { it > 0 } ?: (300 * dm.density).roundToInt()

        var left = 0
        var top = 0
        var right = dm.widthPixels
        var bottom = dm.heightPixels

        val insets = rootView?.rootWindowInsets
        if (insets != null) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                left = bars.left
                top = bars.top
                right = dm.widthPixels - bars.right
                bottom = dm.heightPixels - bars.bottom
            } else {
                @Suppress("DEPRECATION")
                run {
                    left = insets.systemWindowInsetLeft
                    top = insets.systemWindowInsetTop
                    right = dm.widthPixels - insets.systemWindowInsetRight
                    bottom = dm.heightPixels - insets.systemWindowInsetBottom
                }
            }
        }

        val maxX = max(left, right - w)
        val maxY = max(top, bottom - h)
        return OverlayBounds(left, maxX, top, maxY)
    }

    /** Delivers one recognition event into the overlay page. */
    private fun deliverAsrEvent(json: String) {
        webView?.post {
            webView?.evaluateJavascript("window.__vmAsrEvent && window.__vmAsrEvent($json);", null)
        }
    }

    private fun asrEvent(type: String, text: String? = null, reason: String? = null): String {
        val sb = StringBuilder("{\"type\":\"").append(type).append("\"")
        if (text != null) sb.append(",\"text\":\"").append(escapeJson(text)).append("\"")
        if (reason != null) sb.append(",\"reason\":\"").append(escapeJson(reason)).append("\"")
        return sb.append("}").toString()
    }

    private fun escapeJson(raw: String): String {
        val sb = StringBuilder(raw.length + 8)
        for (ch in raw) {
            when {
                ch == '\\' -> sb.append("\\\\")
                ch == '"' -> sb.append("\\\"")
                ch == '\n' -> sb.append("\\n")
                ch == '\r' -> sb.append("\\r")
                ch == '\t' -> sb.append("\\t")
                ch < ' ' -> sb.append(String.format("\\u%04x", ch.code))
                else -> sb.append(ch)
            }
        }
        return sb.toString()
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
        dragLayout = null
        try { asr?.shutdown() } catch (e: Exception) { /* ignore */ }
        asr = null
        try { systemTts?.shutdown() } catch (e: Exception) { /* ignore */ }
        systemTts = null
        NativeTtsRegistry.install(null)
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
        val DEFAULT_BASE_URL: String = BuildConfig.VALLEY_BASE_URL
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
