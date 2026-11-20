package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TtsCallbackTest {

    @Test
    fun `the shim callback names are accepted`() {
        assertTrue(TtsCallback.isValid("vmTts1"))
        assertTrue(TtsCallback.isValid("vmTts42"))
        assertTrue(TtsCallback.isValid("vmTts999999999"))
    }

    @Test
    fun `null is not a valid callback name`() {
        assertFalse(TtsCallback.isValid(null))
    }

    @Test
    fun `JavaScript injection names are rejected`() {
        for (name in listOf(
            "",
            "vmTts",
            "vmTts0;alert(1)",
            "vmTts1;fetch('https://evil.example')",
            "vmTts1\n",
            "vmTts1 ",
            "vmTts-1",
            "vmTts١",
            "vmTts1234567890",
            "xvmTts1",
            "vmTts1]",
        )) {
            assertFalse("accepted $name", TtsCallback.isValid(name))
        }
    }

    @Test
    fun `the completion JavaScript uses a validated name`() {
        assertEquals(
            "window.__vmTtsDone_vmTts7 && window.__vmTtsDone_vmTts7(true);",
            TtsCallback.doneJs("vmTts7", true)
        )
        assertEquals(
            "window.__vmTtsDone_vmTts7 && window.__vmTtsDone_vmTts7(false);",
            TtsCallback.doneJs("vmTts7", false)
        )
    }

    @Test
    fun `no JavaScript is built for an invalid name`() {
        assertNull(TtsCallback.doneJs("vmTts1;alert(1)", true))
        assertNull(TtsCallback.doneJs("", true))
    }
}
