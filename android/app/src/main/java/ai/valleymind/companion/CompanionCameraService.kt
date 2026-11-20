package ai.valleymind.companion

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Base64
import android.view.Gravity
import android.view.WindowManager
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.UseCase
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * CompanionCameraService — the companion's optional camera "eyes".
 *
 * It is a user-started camera foreground service (Android shows the system
 * camera indicator while it runs). CameraX supplies a small live preview window
 * and a single-frame capture used for prompts such as "What am I wearing?" and
 * "Look at this".
 *
 * Deliberately NOT a second brain and NOT an uploader: the captured frame is
 * handed back to the requesting WebView as a data URI and the PAGE posts it to
 * the existing authenticated /api/vision/analyze endpoint. No session, API key
 * or provider logic is duplicated in native code.
 */
class CompanionCameraService : LifecycleService() {

    companion object {
        private const val CHANNEL_ID = "vm_camera_eyes"
        private const val NOTIFICATION_ID = 2402
        const val ACTION_START = "ai.valleymind.companion.camera.START"
        const val ACTION_STOP = "ai.valleymind.companion.camera.STOP"
        const val ACTION_SWITCH = "ai.valleymind.companion.camera.SWITCH"
        const val ACTION_CAPTURE = "ai.valleymind.companion.camera.CAPTURE"
        const val EXTRA_FACING = "facing"
        const val EXTRA_CALLBACK = "callback"

        @Volatile
        private var active = false

        @Volatile
        private var facing = "back"

        @Volatile
        private var lastError: String? = null

        fun isActive(): Boolean = active

        fun stateJson(): String {
            val err = lastError?.let { ",\"error\":\"${jsonSafe(it)}\"" } ?: ""
            return "{\"active\":$active,\"facing\":\"${jsonSafe(facing)}\"$err}"
        }

        private fun jsonSafe(s: String): String = s.replace("\\", "\\\\").replace("\"", "\\\"")

        fun hasPermission(context: Context): Boolean =
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                    PackageManager.PERMISSION_GRANTED

        fun start(context: Context, lens: String? = "back"): Boolean {
            if (!hasPermission(context)) {
                lastError = "camera-permission"
                return false
            }
            val intent = Intent(context, CompanionCameraService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_FACING, lens ?: "back")
            }
            ContextCompat.startForegroundService(context, intent)
            return true
        }

        fun stop(context: Context) {
            val intent = Intent(context, CompanionCameraService::class.java).apply { action = ACTION_STOP }
            context.stopService(intent)
        }

        fun switchLens(context: Context): Boolean {
            if (!active) return false
            ContextCompat.startForegroundService(
                context,
                Intent(context, CompanionCameraService::class.java).apply { action = ACTION_SWITCH }
            )
            return true
        }

        fun capture(context: Context, callbackName: String) {
            ContextCompat.startForegroundService(
                context,
                Intent(context, CompanionCameraService::class.java).apply {
                    action = ACTION_CAPTURE
                    putExtra(EXTRA_CALLBACK, callbackName)
                }
            )
        }
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private lateinit var cameraExecutor: ExecutorService
    private var cameraProvider: ProcessCameraProvider? = null
    private var imageCapture: ImageCapture? = null
    private var previewView: PreviewView? = null
    private var windowManager: WindowManager? = null

    override fun onCreate() {
        super.onCreate()
        cameraExecutor = Executors.newSingleThreadExecutor()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_STOP -> {
                stopEyes()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_SWITCH -> {
                if (active && hasPermission(this)) {
                    startAsForeground()
                    facing = if (facing == "front") "back" else "front"
                    bindCamera()
                }
                return START_STICKY
            }
            ACTION_CAPTURE -> {
                val callback = intent.getStringExtra(EXTRA_CALLBACK)
                if (!callback.isNullOrBlank()) {
                    startAsForeground()
                    captureFrame(callback)
                }
                return START_STICKY
            }
            ACTION_START -> {
                facing = (intent.getStringExtra(EXTRA_FACING) ?: "back").lowercase()
                if (facing != "front") facing = "back"
                startAsForeground()
                if (!hasPermission(this)) {
                    lastError = "camera-permission"
                    pushState()
                    stopSelf()
                    return START_NOT_STICKY
                }
                lastError = null
                attachPreview()
                bindCamera()
            }
            else -> {
                // A restart with no explicit action should not leave a camera
                // running the user did not ask for.
                startAsForeground()
                stopSelf()
                return START_NOT_STICKY
            }
        }
        return START_STICKY
    }

    private fun startAsForeground() {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            )
        } else {
            @Suppress("DEPRECATION")
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(): android.app.Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, CompanionCameraService::class.java).apply { action = ACTION_STOP },
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentTitle("ValleyMind camera eyes")
            .setContentText("Camera is on so your companion can see. Tap to stop.")
            .setOngoing(true)
            .setContentIntent(openApp)
            .addAction(android.R.drawable.ic_delete, "Stop camera", stopIntent)
            .build()
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Companion camera",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Shown only while the companion's camera eyes are switched on"
            setShowBadge(true)
        }
        nm.createNotificationChannel(channel)
    }

    /** Small floating preview. Without overlay permission capture still works. */
    private fun attachPreview() {
        if (previewView != null || !Settings.canDrawOverlays(this)) return
        try {
            val wm = getSystemService(WINDOW_SERVICE) as WindowManager
            val view = PreviewView(this).apply {
                implementationMode = PreviewView.ImplementationMode.COMPATIBLE
                scaleType = PreviewView.ScaleType.FILL_CENTER
            }
            val lp = WindowManager.LayoutParams(
                dp(148), dp(196),
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                        WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                        WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.TOP or Gravity.END
                x = 12
                y = 96
            }
            wm.addView(view, lp)
            windowManager = wm
            previewView = view
        } catch (e: Exception) {
            // Preview is a convenience; frame capture remains available.
            lastError = "preview-unavailable"
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun bindCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            try {
                val provider = providerFuture.get()
                cameraProvider = provider
                val selector = if (facing == "front") {
                    CameraSelector.DEFAULT_FRONT_CAMERA
                } else {
                    CameraSelector.DEFAULT_BACK_CAMERA
                }
                val capture = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                    .build()
                imageCapture = capture

                val useCases = mutableListOf<UseCase>(capture)
                previewView?.let { pv ->
                    val preview = Preview.Builder().build()
                    preview.setSurfaceProvider(pv.surfaceProvider)
                    useCases.add(preview)
                }
                provider.unbindAll()
                provider.bindToLifecycle(this, selector, *useCases.toTypedArray())
                active = true
                lastError = null
                pushState()
            } catch (e: Exception) {
                active = false
                lastError = "camera-bind-failed"
                pushState()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    /** Captures one JPEG frame and returns it to the requesting WebView. */
    fun captureFrame(callbackName: String) {
        val capture = imageCapture
        if (!active || capture == null) {
            deliverError(callbackName, lastError ?: "camera-not-ready")
            return
        }
        val file = File(cacheDir, "vm_eyes_${System.currentTimeMillis()}.jpg")
        val options = ImageCapture.OutputFileOptions.Builder(file).build()
        try {
            capture.takePicture(options, cameraExecutor, object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(results: ImageCapture.OutputFileResults) {
                    try {
                        val bytes = file.readBytes()
                        if (bytes.isEmpty()) throw IllegalStateException("empty-frame")
                        val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
                        val dataUri = "data:image/jpeg;base64,$encoded"
                        mainHandler.post {
                            CameraResultRegistry.deliver(
                                callbackName,
                                CameraResultRegistry.successJson(dataUri)
                            )
                        }
                    } catch (e: Exception) {
                        mainHandler.post { deliverError(callbackName, "capture-encode-failed") }
                    } finally {
                        try { file.delete() } catch (e: Exception) { /* ignore */ }
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    mainHandler.post { deliverError(callbackName, "capture-failed") }
                }
            })
        } catch (e: Exception) {
            deliverError(callbackName, "capture-failed")
        }
    }

    private fun deliverError(callbackName: String, reason: String) {
        CameraResultRegistry.deliver(callbackName, CameraResultRegistry.errorJson(reason))
    }

    private fun pushState() {
        // The camera may be started from either WebView; both subscribe to the
        // process-wide registry, so both receive the state update. The reserved
        // callback name "__cameraState" maps to window.__vmCameraState(json).
        try {
            CameraResultRegistry.deliver(
                "__cameraState",
                "{\"ok\":true,\"state\":" + stateJson() + "}"
            )
        } catch (e: Exception) {
            /* state push is best-effort */
        }
    }

    private fun stopEyes() {
        try { cameraProvider?.unbindAll() } catch (e: Exception) { /* ignore */ }
        cameraProvider = null
        imageCapture = null
        previewView?.let { pv ->
            try { windowManager?.removeView(pv) } catch (e: Exception) { /* ignore */ }
        }
        previewView = null
        windowManager = null
        active = false
        lastError = null
        pushState()
    }

    override fun onDestroy() {
        stopEyes()
        try { cameraExecutor.shutdown() } catch (e: Exception) { /* ignore */ }
        super.onDestroy()
    }
}
