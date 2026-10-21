package ai.valleymind.companion

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

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

    private val appContext = context.applicationContext
    private var tts: TextToSpeech? = null
    private var ready = false
    private var pending: Pair<String, ((Boolean) -> Unit)>? = null

    /** Called when an utterance actually finishes (or fails). */
    private var onDone: ((Boolean) -> Unit)? = null
    /** Last voice ids the device reported, for diagnostics. */
    val availableVoices: List<String> get() = try { tts?.voices?.map { it.name } ?: emptyList() } catch (e: Exception) { emptyList() }

    fun init() {
        if (tts != null) return
        tts = TextToSpeech(appContext) { status ->
            ready = status == TextToSpeech.SUCCESS
            try { tts?.language = Locale.US } catch (e: Exception) { /* engine may refuse */ }
            tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) { /* mouth opens in speak() */ }
                override fun onDone(utteranceId: String?) { finish(true) }
                @Deprecated("kept for API < 21 compatibility of the listener contract")
                override fun onError(utteranceId: String?) { finish(false) }
                override fun onError(utteranceId: String?, errorCode: Int) { finish(false) }
            })
            val queued = pending
            pending = null
            if (queued != null) speak(queued.first, null, queued.second)
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
            pending = clean to done
            return
        }
        if (voiceId != null) {
            try {
                val match = tts?.voices?.firstOrNull { it.name.equals(voiceId, ignoreCase = true) }
                if (match != null) tts?.voice = match
            } catch (e: Exception) { /* keep the default voice */ }
        }
        onDone = done
        val id = "vm-" + System.currentTimeMillis()
        val result = try {
            tts?.speak(clean, TextToSpeech.QUEUE_FLUSH, null, id)
        } catch (e: Exception) {
            TextToSpeech.ERROR
        }
        if (result == TextToSpeech.ERROR) finish(false)
    }

    fun stop() {
        try { tts?.stop() } catch (e: Exception) { /* ignore */ }
        finish(false)
    }

    fun shutdown() {
        try { tts?.stop() } catch (e: Exception) { /* ignore */ }
        try { tts?.shutdown() } catch (e: Exception) { /* ignore */ }
        tts = null
        ready = false
        pending = null
        onDone = null
    }

    /** Maps one of the four companion profile ids to a best-effort voice id. */
    fun voiceIdForProfile(profile: String?): String? = mapProfileToVoice(profile)

    companion object {
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
        val cb = onDone
        onDone = null
        if (cb != null) {
            try { cb(ok) } catch (e: Exception) { /* ignore */ }
        }
    }
}
