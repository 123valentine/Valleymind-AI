package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SystemTtsTest {

    @Test
    fun `each companion profile maps to a distinct voice id`() {
        val marcus = SystemTts.mapProfileToVoice("marcus")
        val angelina = SystemTts.mapProfileToVoice("angelina")
        val elena = SystemTts.mapProfileToVoice("elena")
        val companion = SystemTts.mapProfileToVoice("companion")
        assertEquals("en-us-x-iol-local", marcus)
        assertEquals("en-us-x-sfg-local", angelina)
        assertEquals("en-us-x-tpf-local", elena)
        assertEquals("en-us-x-tpd-local", companion)
        // No profile may silently share another's voice.
        val all = listOf(marcus, angelina, elena, companion)
        assertEquals(4, all.toSet().size)
    }

    @Test
    fun `mapping is case and whitespace tolerant`() {
        assertEquals(SystemTts.mapProfileToVoice("marcus"), SystemTts.mapProfileToVoice("  MARCUS "))
        assertEquals(SystemTts.mapProfileToVoice("companion"), SystemTts.mapProfileToVoice("Companion"))
    }

    @Test
    fun `semantic voice requests resolve female by default`() {
        assertEquals("female", SystemTts.voiceRequestForProfile("elena"))
        assertEquals("female", SystemTts.voiceRequestForProfile("angelina"))
        assertEquals("female", SystemTts.voiceRequestForProfile("companion"))
        assertEquals("male", SystemTts.voiceRequestForProfile("marcus"))
        assertNull(SystemTts.voiceRequestForProfile("unknown"))

        assertTrue(SystemTts.isFemaleVoiceRequest(" FEMALE "))
        assertTrue(SystemTts.isFemaleVoiceRequest("elena"))
        assertFalse(SystemTts.isFemaleVoiceRequest("male"))
        assertTrue(SystemTts.isMaleVoiceRequest(" male "))
        assertTrue(SystemTts.isMaleVoiceRequest("marcus"))
        assertFalse(SystemTts.isMaleVoiceRequest("female"))
    }

    @Test
    fun `unknown or empty profile returns null for the engine default`() {
        assertNull(SystemTts.mapProfileToVoice(null))
        assertNull(SystemTts.mapProfileToVoice(""))
        assertNull(SystemTts.mapProfileToVoice("unknown"))
        assertNull(SystemTts.mapProfileToVoice("  "))
    }
}
