package ai.valleymind.companion

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import java.util.Locale
import kotlin.math.PI
import kotlin.math.sin

/**
 * SystemTts — a thin wrapper over Android's own TextToSpeech engine.
 *
 * Android's WebView has no `speechSynthesis` (it is not part of the Chromium
 * build that ships in WebView), so the page's browser-speech fallback cannot
 * speak inside the floating overlay. This is the native fallback that keeps the
 * companion audible: it is free, offline, uses the voices already installed on
 * the device, and needs no API key.
 *
 * It is NOT a second voice system: the page still prefers the site's
 * VMCloudVoice (server TTS -> real-amplitude lip-sync) and only falls back here
 * when that path is unavailable. The mouth in this fallback is event-driven
 * (open at utterance start, close at end), which the overlay discloses rather
 * than disguising as amplitude sync.
 *
 * Threading: TextToSpeech must be created and used on the main thread. Speak
 * calls are posted to it; the speaking callback hops back to the caller via the
 * OnSpeaking listener, which the bridge marshals to the WebView thread.
 */
class SystemTts(context: Context) {

    /**
     * Voice lifecycle delivered to the web companion. Android's TextToSpeech
     * has no amplitude stream, so onLevel is a speech-shaped envelope emitted
     * only between the engine's real onStart and onEnd callbacks. The web layer
     * still uses TRUE amplitude when the site's server TTS is available; this
     * is explicitly the native fallback, never a claim of measured amplitude.
     */
    interface SpeakingListener {
        fun onStart(utteranceId: String)
        fun onLevel(level: Float)
        fun onEnd(utteranceId: String, ok: Boolean)
    }

    private val appContext = context.applicationContext
    private var tts: TextToSpeech? = null
    private var ready = false
    private var pending: Triple<String, String?, ((Boolean) -> Unit)>? = null

    /** Called when an utterance actually finishes (or fails). */
    private var onDone: ((Boolean) -> Unit)? = null
    var speakingListener: SpeakingListener? = null

    private val levelHandler = Handler(Looper.getMainLooper())
    private var levelRunnable: Runnable? = null
    private var levelStartMs = 0L
    private var levelSeed = 0
    private var currentUtteranceId = ""

    /** Last voice ids the device reported, for diagnostics. */
    val availableVoices: List<String> get() = try { tts?.voices?.map { it.name } ?: emptyList() } catch (e: Exception) { emptyList() }

    fun init() {
        if (tts != null) return
        tts = TextToSpeech(appContext) { status ->
            ready = status == TextToSpeech.SUCCESS
            try { tts?.language = Locale.US } catch (e: Exception) { /* engine may refuse */ }
            tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {
                    val id = utteranceId ?: ""
                    currentUtteranceId = id
                    speakingListener?.onStart(id)
                    startLevelTicker(id)
                }
                override fun onDone(utteranceId: String?) { finish(true) }
                @Deprecated("kept for API < 21 compatibility of the listener contract")
                override fun onError(utteranceId: String?) { finish(false) }
                override fun onError(utteranceId: String?, errorCode: Int) { finish(false) }
            })
            val queued = pending
            pending = null
            if (queued != null) speak(queued.first, queued.second, queued.third)
        }
    }

    /**
     * Speaks [text]. [voiceId] selects one of the device's installed voices
     * (best-effort: an unknown id falls back to the engine default, because
     * voice ids differ between devices and Android versions). [done] fires once
     * with true (finished) or false (failed / no engine).
     */
    fun speak(text: String, voiceId: String?, done: (Boolean) -> Unit) {
        val clean = text.trim()
        if (clean.isEmpty()) { done(false); return }
        init()
        if (!ready) {
            // Engine still initialising: queue the one utterance we care about.
            pending = Triple(clean, voiceId, done)
            return
        }
        selectVoice(voiceId)
        onDone = done
        val id = "vm-" + System.currentTimeMillis()
        val result = try {
            tts?.speak(clean, TextToSpeech.QUEUE_FLUSH, null, id)
        } catch (e: Exception) {
            TextToSpeech.ERROR
        }
        if (result == TextToSpeech.ERROR) finish(false)
    }

    /**
     * Resolves a semantic request ("female" / "male" / a profile name) against
     * the voices the device actually reports. A natural female voice wins over
     * the engine default; when the engine exposes no female voice (common on a
     * single-voice device) the pitch is nudged to 1.08f so the companion no
     * longer sounds like the default male voice. Everything stays inside the
     * ONE native TextToSpeech engine.
     */
    private fun selectVoice(requested: String?) {
        val engine = tts ?: return
        val token = requested?.trim().orEmpty()
        val wantFemale = isFemaleVoiceRequest(token)
        val wantMale = isMaleVoiceRequest(token)
        try {
            val voices = engine.voices?.toList().orEmpty()
            var chosen: Voice? = null
            if (token.isNotEmpty()) {
                chosen = voices.firstOrNull { it.name.equals(token, ignoreCase = true) }
                // Only profile-style tokens use substring matching: "male" must
                // never pick a voice whose name merely contains "female".
                if (chosen == null && !wantFemale && !wantMale) {
                    chosen = voices.firstOrNull { it.name.contains(token, ignoreCase = true) }
                }
            }
            if (chosen == null && wantFemale) {
                chosen = voices.firstOrNull { isFemaleVoice(it) && isEnglishVoice(it) }
                    ?: voices.firstOrNull { isFemaleVoice(it) }
            }
            if (chosen == null && wantMale) {
                chosen = voices.firstOrNull { !isFemaleVoice(it) && isEnglishVoice(it) }
                    ?: voices.firstOrNull { !isFemaleVoice(it) }
            }
            if (chosen != null) {
                engine.voice = chosen
                engine.setPitch(1.0f)
            } else if (wantFemale) {
                // Single/default-voice engine: no female sample is available.
                engine.setPitch(1.08f)
            } else {
                engine.setPitch(1.0f)
            }
        } catch (e: Exception) {
            if (wantFemale) {
                try { engine.setPitch(1.08f) } catch (ignored: Exception) { /* keep engine default */ }
            }
        }
    }

    private fun isEnglishVoice(voice: Voice): Boolean =
        try { voice.locale.language.equals("en", ignoreCase = true) }
        catch (e: Exception) { false }

    /** Best-effort female detection across common Android/Google voice names. */
    private fun isFemaleVoice(voice: Voice): Boolean {
        val name = try { voice.name.lowercase(Locale.US) } catch (e: Exception) { return false }
        if (name.contains("female") || name.contains("woman") || name.contains("girl")) return true
        if (name.contains("male")) return false
        val hints = listOf(
            "zira", "hazel", "samantha", "victoria", "serena", "moira", "tessa",
            "karen", "susan", "michelle", "stefanie", "kate", "amelie", "catherine",
            "aria", "jenny", "elena", "angelina", "sfg", "tpf", "smtf", "f00", "f01"
        )
        return hints.any { name.contains(it) }
    }

    fun stop() {
        try { tts?.stop() } catch (e: Exception) { /* ignore */ }
        finish(false)
    }

    fun shutdown() {
        stopLevelTicker()
        try { tts?.stop() } catch (e: Exception) { /* ignore */ }
        try { tts?.shutdown() } catch (e: Exception) { /* ignore */ }
        tts = null
        ready = false
        pending = null
        onDone = null
        speakingListener = null
    }

    /** Emits a bounded, syllable-shaped level between the engine's start/end. */
    private fun startLevelTicker(id: String) {
        stopLevelTicker()
        levelStartMs = System.currentTimeMillis()
        levelSeed = id.hashCode()
        val tick = object : Runnable {
            override fun run() {
                val t = (System.currentTimeMillis() - levelStartMs) / 1000f
                val syllable = 0.5f + 0.5f * sin(t * 2f * PI.toFloat() * 4.5f + levelSeed)
                val phrase = 0.55f + 0.45f * sin(t * 2f * PI.toFloat() * 0.35f + 0.7f)
                val level = (0.08f + 0.87f * syllable * phrase).coerceIn(0f, 1f)
                speakingListener?.onLevel(level)
                levelRunnable?.let { levelHandler.postDelayed(it, 70L) }
            }
        }
        levelRunnable = tick
        levelHandler.post(tick)
    }

    private fun stopLevelTicker() {
        levelRunnable?.let { levelHandler.removeCallbacks(it) }
        levelRunnable = null
    }

    /** Maps one of the four companion profile ids to a best-effort voice id. */
    fun voiceIdForProfile(profile: String?): String? = mapProfileToVoice(profile)

    companion object {
        /** Semantic voice request used by the web surfaces and the greeting. */
        fun voiceRequestForProfile(profile: String?): String? = when ((profile ?: "").trim().lowercase()) {
            "marcus" -> "male"
            "elena", "angelina", "companion" -> "female"
            else -> null
        }

        fun isFemaleVoiceRequest(request: String?): Boolean = when ((request ?: "").trim().lowercase()) {
            "female", "feminine", "elena", "angelina", "companion" -> true
            else -> false
        }

        fun isMaleVoiceRequest(request: String?): Boolean = when ((request ?: "").trim().lowercase()) {
            "male", "masculine", "marcus" -> true
            else -> false
        }

        /**
         * Best-effort mapping from a companion voice profile to an installed
         * Android TTS voice id. These ids are the common "local" en-US voices on
         * modern Android; SystemTts.speak falls back to the engine default when a
         * device does not expose one, so an unknown id is never fatal.
         */
        fun mapProfileToVoice(profile: String?): String? = when ((profile ?: "").trim().lowercase()) {
            "marcus"    -> "en-us-x-iol-local"
            "angelina"  -> "en-us-x-sfg-local"
            "elena"     -> "en-us-x-tpf-local"
            "companion" -> "en-us-x-tpd-local"
            else -> null
        }
    }

    private fun finish(ok: Boolean) {
        stopLevelTicker()
        val listener = speakingListener
        speakingListener = null
        if (listener != null) {
            try { listener.onEnd(currentUtteranceId, ok) } catch (e: Exception) { /* ignore */ }
        }
        val cb = onDone
        onDone = null
        if (cb != null) {
            try { cb(ok) } catch (e: Exception) { /* ignore */ }
        }
    }
}
