package ai.valleymind.companion

import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

/**
 * CompanionAsr — Android's own SpeechRecognizer, driven from the native side.
 *
 * WHY THIS EXISTS
 * Android's WebView has no Web Speech API (`window.SpeechRecognition` and
 * `window.webkitSpeechRecognition` are both undefined in the WebView build),
 * so the overlay page cannot recognise speech in-page. The only browser-side
 * alternative is MediaRecorder + an upload to the server's transcription
 * endpoint, which sends the raw microphone audio off the device and detects the
 * end of speech with a crude energy threshold.
 *
 * SpeechRecognizer is the platform's own mechanism: it runs the microphone
 * capture inside the OS recogniser service, delivers an explicit
 * onEndOfSpeech() signal, and the audio never has to leave the device to be
 * turned into text. It is free, offline-capable and adds no dependency.
 *
 * This class is a thin, fail-closed wrapper. It owns exactly ONE recogniser,
 * refuses to start a second one while one is running, and reports only real
 * platform events. The page owns the conversation loop; this class only
 * recognises.
 *
 * Threading: SpeechRecognizer must be created and driven on the main thread.
 * Every entry point therefore posts to the main looper, and the listener
 * callbacks are marshalled back to the caller the same way.
 */
class CompanionAsr(context: Context) {

    /**
     * The events the page receives. Kept as plain strings so the JavaScript
     * side never has to know about Android error codes.
     */
    interface Listener {
        /** The recogniser is listening. */
        fun onAsrReady()

        /** A partial (still changing) transcript. */
        fun onAsrPartial(text: String)

        /** The platform decided the utterance is over. */
        fun onAsrEndOfSpeech()

        /** The final transcript. */
        fun onAsrFinal(text: String)

        /** A failure. [reason] is a short, stable, page-safe token. */
        fun onAsrError(reason: String)
    }

    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var listening = false
    private var listener: Listener? = null

    // Voice-processing chain attached to a VOICE_RECOGNITION audio session for
    // the lifetime of one utterance. The AudioRecord is never started: it only
    // owns the audio session the platform effects attach to.
    private var voiceProcessingRecord: AudioRecord? = null
    private var automaticGainControl: AutomaticGainControl? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var acousticEchoCanceler: AcousticEchoCanceler? = null

    /** True when the platform actually offers a speech recogniser. */
    val supported: Boolean
        get() = try {
            SpeechRecognizer.isRecognitionAvailable(appContext)
        } catch (e: Exception) {
            false
        }

    /** True while the recogniser is capturing. */
    val isListening: Boolean get() = listening

    fun setListener(l: Listener?) {
        listener = l
    }

    /**
     * Returns the recogniser, creating it on first use.
     *
     * The public API is createSpeechRecognizer(context) plus
     * setRecognitionListener(...); the two-argument overload takes a
     * ComponentName and is not for apps. Exactly one recogniser is kept for the
     * lifetime of the session: creating one per utterance leaks platform
     * resources.
     */
    private fun ensureRecognizer(): SpeechRecognizer? {
        recognizer?.let { return it }
        return try {
            val rec = SpeechRecognizer.createSpeechRecognizer(appContext)
            rec.setRecognitionListener(callback)
            recognizer = rec
            rec
        } catch (e: Exception) {
            null
        }
    }

    /**
     * Attaches Android's voice-processing effects to a VOICE_RECOGNITION audio
     * session. AGC, noise suppression and acoustic echo cancellation are all
     * optional (device/API dependent); an unavailable effect is simply skipped
     * and never blocks recognition. The holder AudioRecord is never started, so
     * it cannot contend with SpeechRecognizer for the microphone.
     */
    private fun attachVoiceProcessing() {
        releaseVoiceProcessing()
        try {
            val sampleRate = VOICE_PROCESSING_SAMPLE_RATE
            val channelConfig = AudioFormat.CHANNEL_IN_MONO
            val encoding = AudioFormat.ENCODING_PCM_16BIT
            val minBuffer = AudioRecord.getMinBufferSize(sampleRate, channelConfig, encoding)
            if (minBuffer <= 0) return

            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                sampleRate,
                channelConfig,
                encoding,
                minBuffer * 2
            )
            val session = record.audioSessionId
            if (session == 0) {
                record.release()
                return
            }
            voiceProcessingRecord = record

            if (AutomaticGainControl.isAvailable()) {
                val effect = AutomaticGainControl.create(session)
                if (effect != null) {
                    try { effect.enabled = true } catch (e: Exception) { /* keep device default */ }
                    automaticGainControl = effect
                }
            }
            if (NoiseSuppressor.isAvailable()) {
                val effect = NoiseSuppressor.create(session)
                if (effect != null) {
                    try { effect.enabled = true } catch (e: Exception) { /* keep device default */ }
                    noiseSuppressor = effect
                }
            }
            if (AcousticEchoCanceler.isAvailable()) {
                val effect = AcousticEchoCanceler.create(session)
                if (effect != null) {
                    try { effect.enabled = true } catch (e: Exception) { /* keep device default */ }
                    acousticEchoCanceler = effect
                }
            }
        } catch (e: Exception) {
            // Voice processing is best-effort: SpeechRecognizer still works.
        }
    }

    /** Releases the optional effect chain after the utterance completes. */
    private fun releaseVoiceProcessing() {
        try { automaticGainControl?.release() } catch (e: Exception) { /* ignore */ }
        try { noiseSuppressor?.release() } catch (e: Exception) { /* ignore */ }
        try { acousticEchoCanceler?.release() } catch (e: Exception) { /* ignore */ }
        automaticGainControl = null
        noiseSuppressor = null
        acousticEchoCanceler = null
        try { voiceProcessingRecord?.release() } catch (e: Exception) { /* ignore */ }
        voiceProcessingRecord = null
    }

    /**
     * Starts recognition. Returns false when it could not be started (no
     * recogniser, already listening, or the platform refused) — the caller must
     * never assume the microphone is open.
     */
    fun start(locale: String = "en-US"): Boolean {
        mainHandler.post {
            if (listening) return@post
            if (!supported) {
                emitError("unsupported")
                return@post
            }
            val r = ensureRecognizer()
            if (r == null) {
                emitError("unsupported")
                return@post
            }
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                // Longer silence windows stop the recogniser cutting the user off
                // mid-thought on a noisy phone call, while still ending the turn.
                putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1800L)
                putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1400L)
                putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, MediaRecorder.AudioSource.VOICE_RECOGNITION)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, appContext.packageName)
            }
            attachVoiceProcessing()
            try {
                r.startListening(intent)
                listening = true
                listener?.onAsrReady()
            } catch (e: Exception) {
                listening = false
                releaseVoiceProcessing()
                emitError("client")
            }
        }
        return true
    }

    /**
     * Stops recognition but keeps the final result: the platform finishes the
     * utterance and still delivers onResults(). This is the normal way to end
     * a turn.
     */
    fun stop() {
        mainHandler.post {
            val r = recognizer ?: return@post
            try {
                r.stopListening()
            } catch (e: Exception) {
                listening = false
                emitError("client")
            }
        }
    }

    /** Abandons recognition: no result is delivered. */
    fun cancel() {
        mainHandler.post {
            val r = recognizer ?: return@post
            listening = false
            releaseVoiceProcessing()
            try {
                r.cancel()
            } catch (e: Exception) {
                /* nothing to do: the recogniser is already gone */
            }
        }
    }

    /** Releases the recogniser. The page calls this when the session ends. */
    fun shutdown() {
        mainHandler.post {
            listening = false
            releaseVoiceProcessing()
            recognizer?.let { r ->
                try {
                    r.destroy()
                } catch (e: Exception) {
                    /* ignore */
                }
            }
            recognizer = null
        }
    }

    private fun emitError(reason: String) {
        val l = listener ?: return
        mainHandler.post { l.onAsrError(reason) }
    }

    private fun firstResult(results: Bundle?): String {
        if (results == null) return ""
        val list = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
        if (list == null || list.isEmpty()) return ""
        return list.firstOrNull()?.trim() ?: ""
    }

    private val callback = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {
            mainHandler.post { listener?.onAsrReady() }
        }

        override fun onBeginningOfSpeech() { /* the page shows its own state */ }

        override fun onRmsChanged(rmsdB: Float) { /* unused: no level meter */ }

        override fun onBufferReceived(buffer: ByteArray?) { /* raw audio is discarded */ }

        override fun onEndOfSpeech() {
            mainHandler.post { listener?.onAsrEndOfSpeech() }
        }

        override fun onError(error: Int) {
            listening = false
            releaseVoiceProcessing()
            val reason = reasonFor(error)
            mainHandler.post { listener?.onAsrError(reason) }
        }

        override fun onResults(results: Bundle?) {
            listening = false
            releaseVoiceProcessing()
            val text = firstResult(results)
            mainHandler.post {
                if (text.isEmpty()) listener?.onAsrError("no-match") else listener?.onAsrFinal(text)
            }
        }

        override fun onPartialResults(partialResults: Bundle?) {
            val text = firstResult(partialResults)
            if (text.isEmpty()) return
            mainHandler.post { listener?.onAsrPartial(text) }
        }

        override fun onEvent(eventType: Int, params: Bundle?) { /* not used */ }
    }

    companion object {
        /** Standard speech sample rate for the optional voice-effect session. */
        private const val VOICE_PROCESSING_SAMPLE_RATE = 16000

        /** Builds the exact JSON event the injected shim forwards to the page. */
        fun eventJson(type: String, text: String? = null, reason: String? = null): String {
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
                    ch < ' ' -> sb.append(String.format(java.util.Locale.ROOT, "\\u%04x", ch.code))
                    else -> sb.append(ch)
                }
            }
            return sb.toString()
        }

        /**
         * Maps an Android recognition error onto a small, stable token the page
         * can act on. The raw code is never surfaced to the page.
         */
        fun reasonFor(error: Int): String = when (error) {
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "permission"
            SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "network"
            SpeechRecognizer.ERROR_NO_MATCH -> "no-match"
            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "timeout"
            SpeechRecognizer.ERROR_AUDIO -> "audio"
            SpeechRecognizer.ERROR_SERVER -> "server"
            SpeechRecognizer.ERROR_CLIENT -> "client"
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "busy"
            SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> "busy"
            else -> "unknown"
        }
    }
}
