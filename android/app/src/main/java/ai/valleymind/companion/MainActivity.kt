package ai.valleymind.companion

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
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
import androidx.core.content.FileProvider
import java.io.File
import java.io.FileOutputStream

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

    /** Native TTS fallback (Android WebView has no speechSynthesis). */
    private lateinit var systemTts: SystemTts

    /** The origin the WebView loads; production default, runtime-overridable. */
    private var baseUrl: String = BuildConfig.VALLEY_BASE_URL

    private val REQUEST_CODE_FILE_CHOOSER = 1002
    private val REQUEST_CODE_CAMERA_CAPTURE = 1003
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var cameraImageUri: Uri? = null
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

    /** Microphone permission for tap-to-talk (mic / getUserMedia in the WebView). */
    private val recordAudioPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* the page's getUserMedia/recognition retries on the next tap */ }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        configStore = AppConfigStore(this)
        baseUrl = configStore.getBaseUrl(BuildConfig.VALLEY_BASE_URL)

        systemTts = SystemTts(this)
        systemTts.init()
        // The native-TTS completion callback is evaluated in this WebView.
        NativeTtsRegistry.install { name, ok ->
            val js = "window.__vmTtsDone_" + name + " && window.__vmTtsDone_" + name + "(" + ok + ");"
            webView.post { webView.evaluateJavascript(js, null) }
        }

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
        maybeRequestRecordAudio()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQUEST_CODE_FILE_CHOOSER -> handleFileChooserResult(resultCode, data)
            REQUEST_CODE_CAMERA_CAPTURE -> handleCameraCaptureResult(resultCode, data)
        }
    }

    /**
     * Result of the system file/media picker. A cancelled chooser must still
     * answer the WebView callback, otherwise the page's file input stays "open"
     * and the composer never recovers.
     */
    private fun handleFileChooserResult(resultCode: Int, data: Intent?) {
        val callback = filePathCallback
        filePathCallback = null
        if (callback == null) return

        if (resultCode != Activity.RESULT_OK) {
            callback.onReceiveValue(null)
            return
        }

        val results: Array<Uri>? = try {
            val clip = data?.clipData
            when {
                clip != null && clip.itemCount > 0 -> {
                    val uris = mutableListOf<Uri>()
                    for (i in 0 until clip.itemCount) {
                        clip.getItemAt(i)?.uri?.let { uris.add(it) }
                    }
                    if (uris.isNotEmpty()) uris.toTypedArray() else null
                }
                data?.data != null -> arrayOf(data.data!!)
                else -> null
            }
        } catch (_: Exception) {
            null
        }
        callback.onReceiveValue(results)
    }

    /**
     * Result of the native camera capture (ACTION_IMAGE_CAPTURE). The photo is
     * normally written to the FileProvider URI passed via EXTRA_OUTPUT; a few
     * camera apps ignore it and return a thumbnail bitmap instead, so both
     * shapes are handled. Cancelling always answers the callback.
     */
    @Suppress("DEPRECATION")
    private fun handleCameraCaptureResult(resultCode: Int, data: Intent?) {
        val callback = filePathCallback
        filePathCallback = null
        val outputUri = cameraImageUri
        cameraImageUri = null

        if (callback == null) {
            deleteCameraFile(outputUri)
            return
        }
        if (resultCode != Activity.RESULT_OK) {
            callback.onReceiveValue(null)
            deleteCameraFile(outputUri)
            return
        }

        var result: Uri? = null
        if (outputUri != null && contentHasBytes(outputUri)) {
            result = outputUri
        } else {
            // Bundle.get(String) is deprecated but is the only shape older
            // camera apps use for the thumbnail fallback.
            val bitmap = try {
                data?.extras?.get("data") as? Bitmap
            } catch (_: Exception) {
                null
            }
            if (bitmap != null) result = writeCameraBitmap(bitmap)
        }

        if (result == null) {
            deleteCameraFile(outputUri)
            callback.onReceiveValue(null)
        } else {
            callback.onReceiveValue(arrayOf(result))
        }
    }

    /** Private cache directory shared with res/xml/file_paths.xml. */
    private fun cameraOutputDir(): File = File(cacheDir, "camera").apply { if (!exists()) mkdirs() }

    private fun newCameraOutputUri(): Uri? {
        return try {
            val dir = cameraOutputDir()
            // Captures whose URI the page never consumed would otherwise pile up.
            val cutoff = System.currentTimeMillis() - 24L * 60L * 60L * 1000L
            dir.listFiles()?.forEach { f -> if (f.lastModified() < cutoff) f.delete() }
            val file = File.createTempFile("capture_", ".jpg", dir)
            FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        } catch (_: Exception) {
            null
        }
    }

    private fun contentHasBytes(uri: Uri): Boolean {
        return try {
            contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length > 0 } ?: false
        } catch (_: Exception) {
            false
        }
    }

    private fun writeCameraBitmap(bitmap: Bitmap): Uri? {
        return try {
            val file = File.createTempFile("capture_", ".jpg", cameraOutputDir())
            FileOutputStream(file).use { out -> bitmap.compress(Bitmap.CompressFormat.JPEG, 92, out) }
            FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        } catch (_: Exception) {
            null
        }
    }

    private fun deleteCameraFile(uri: Uri?) {
        if (uri == null) return
        try {
            val name = uri.lastPathSegment ?: return
            val file = File(cameraOutputDir(), name)
            if (file.exists()) file.delete()
        } catch (_: Exception) {
            /* best effort */
        }
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
        // getUserMedia / Web Speech recognition inside a WebView are denied
        // unless the app explicitly grants them here. The main app only ever
        // needs the microphone, and only when the app already holds RECORD_AUDIO.
        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest?) {
                if (request == null) return
                val wantsAudio = request.resources?.any {
                    it == PermissionRequest.RESOURCE_AUDIO_CAPTURE
                } == true
                val granted = ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED
                if (wantsAudio && granted) request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
                else request.deny()
            }

            override fun onShowFileChooser(
                webView: WebView,
                newFilePathCallback: ValueCallback<Array<Uri>>,
                fileChooserParams: FileChooserParams
            ): Boolean {
                // Cancel any pending callback before starting a new chooser: a
                // WebView input can only have one picker request open at a time.
                filePathCallback?.onReceiveValue(null)
                filePathCallback = newFilePathCallback
                deleteCameraFile(cameraImageUri)
                cameraImageUri = null

                val accepts = fileChooserParams.acceptTypes?.filter { it.isNotBlank() } ?: emptyList()
                val imageOnly = accepts.isNotEmpty() && accepts.all { it.startsWith("image/") }
                val captureRequested = try { fileChooserParams.isCaptureEnabled } catch (_: Exception) { false }

                // "Camera" (accept="image/*" capture) opens the system camera with a
                // FileProvider output URI. If no camera app is installed, fall through
                // to the normal chooser so the callback is always answered.
                if (captureRequested && imageOnly) {
                    val outputUri = newCameraOutputUri()
                    if (outputUri != null) {
                        val captureIntent = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
                            putExtra(MediaStore.EXTRA_OUTPUT, outputUri)
                            // Request the rear (environment) camera by default, not the front selfie camera.
                            putExtra("android.intent.extra.USE_FRONT_CAMERA", false)
                            addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        }
                        if (captureIntent.resolveActivity(packageManager) != null) {
                            cameraImageUri = outputUri
                            return try {
                                startActivityForResult(captureIntent, REQUEST_CODE_CAMERA_CAPTURE)
                                true
                            } catch (_: Exception) {
                                deleteCameraFile(outputUri)
                                cameraImageUri = null
                                val cb = filePathCallback
                                filePathCallback = null
                                cb?.onReceiveValue(null)
                                false
                            }
                        }
                    }
                    deleteCameraFile(outputUri)
                }

                return try {
                    // createIntent() already targets ACTION_GET_CONTENT with
                    // CATEGORY_OPENABLE; adding more categories here would hide
                    // gallery/camera apps that return non-openable URIs.
                    val intent = fileChooserParams.createIntent().apply {
                        if (fileChooserParams.mode == FileChooserParams.MODE_OPEN_MULTIPLE) {
                            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                        }
                        // Respect the accept attribute when it is a single MIME type.
                        val only = accepts.singleOrNull()
                        if (type.isNullOrBlank() || type == "*/*") {
                            type = when {
                                accepts.isEmpty() -> "*/*"
                                only != null && only.contains("/") -> only
                                else -> "*/*"
                            }
                        }
                    }
                    startActivityForResult(Intent.createChooser(intent, "Select file"), REQUEST_CODE_FILE_CHOOSER)
                    true
                } catch (e: Exception) {
                    val cb = filePathCallback
                    filePathCallback = null
                    cb?.onReceiveValue(null)
                    false
                }
            }
        }
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
        // Never leave the WebView waiting on a callback that can no longer fire.
        filePathCallback?.onReceiveValue(null)
        filePathCallback = null
        cameraImageUri = null
        if (::systemTts.isInitialized) systemTts.shutdown()
        NativeTtsRegistry.install(null)
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

    /**
     * Ask for the microphone at launch. The overlay service cannot request a
     * runtime permission (only an Activity can), so doing it here means the
     * floating companion's onPermissionRequest can grant audio capture the moment
     * the user taps to talk. Declining simply leaves tap-to-talk unavailable.
     */
    private fun maybeRequestRecordAudio() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            recordAudioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // ── CompanionBridge.Host: native TTS fallback ───────────────────

    override fun speakNative(text: String, voiceId: String?, done: (Boolean) -> Unit) {
        systemTts.speak(text, voiceId, done)
    }

    override fun stopNativeSpeech() {
        systemTts.stop()
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
