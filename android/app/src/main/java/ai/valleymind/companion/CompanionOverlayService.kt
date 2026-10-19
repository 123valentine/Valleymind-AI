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
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout
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
    private var params: WindowManager.LayoutParams? = null
    private var baseUrl: String = DEFAULT_BASE_URL

    private val overlayHost = object : CompanionBridge.Host {
        override fun isOverlaySupported(): Boolean = true
        override fun hasOverlayPermission(): Boolean = canDrawOverlays()
        override fun requestOverlayPermission() { /* granted in the main app */ }
        override fun overlayStateJson(): String = stateJson()
        override fun setOverlayVisible(on: Boolean) {
            if (on) CompanionOverlayService.start(this@CompanionOverlayService, baseUrl)
            else CompanionOverlayService.stop(this@CompanionOverlayService)
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
        detachOverlay()
        super.onDestroy()
    }

    // ── overlay window ──────────────────────────────────────────────

    private fun showOverlay() {
        if (rootView != null) { isVisible = true; isRunning = true; return }
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
            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                view?.evaluateJavascript(AndroidBridgeJs.ANDROID_BRIDGE_JS, null)
            }
            override fun onPageFinished(view: WebView?, url: String?) {
                view?.evaluateJavascript(AndroidBridgeJs.ANDROID_BRIDGE_JS, null)
                pushStateToOverlay()
            }
        }
        wv.loadUrl("$baseUrl/static/companion_overlay.html")
        webView = wv

        // A slim drag handle; the character area below stays interactive.
        val drag = View(this).apply { setBackgroundColor(0x22000000) }
        wireDrag(drag)

        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(drag, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, (26 * density).roundToInt()))
            addView(wv, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
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
        params = null
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
