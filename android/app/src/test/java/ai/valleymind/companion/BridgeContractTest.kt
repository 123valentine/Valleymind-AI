package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeContractTest {

    @Test
    fun `the contract version is explicit`() {
        assertEquals(2, BridgeContract.VERSION)
    }

    @Test
    fun `frozen legacy native methods are still declared`() {
        for (method in listOf(
            "isOverlaySupported",
            "hasPermission",
            "requestPermission",
            "getStateJson",
            "setVisible",
            "notifyBridgeReady",
            "ttsSpeak",
            "ttsStop",
            "asrSupported",
            "asrStart",
            "asrStop",
            "asrCancel",
        )) {
            assertTrue("missing native method $method", BridgeContract.NATIVE_METHODS.contains(method))
        }
    }

    @Test
    fun `frozen shim methods are still declared`() {
        for (method in listOf(
            "isSupported",
            "hasPermission",
            "requestPermission",
            "getState",
            "getVisible",
            "setVisible",
            "onCompanionVisibility",
            "hasNativeTts",
            "ttsSpeak",
            "ttsStop",
            "hasNativeAsr",
            "asrStart",
            "asrStop",
            "asrCancel",
            "onAsrEvent",
        )) {
            assertTrue("missing shim method $method", BridgeContract.SHIM_METHODS.contains(method))
        }
    }

    @Test
    fun `new capabilities are advertised`() {
        assertEquals(
            listOf("overlay", "overlayRegion", "tts", "asr", "files"),
            BridgeContract.CAPABILITIES
        )
        assertEquals(
            "[\"overlay\",\"overlayRegion\",\"tts\",\"asr\",\"files\"]",
            BridgeContract.capabilitiesJson()
        )
    }

    @Test
    fun `events and overlay markers are unchanged`() {
        assertEquals(
            listOf("__vmAndroidState", "__vmAsrEvent", "__vmTtsDone_"),
            BridgeContract.EVENTS
        )
        assertEquals(listOf("vmOverlayRoot", "stage", "btnHide"), BridgeContract.OVERLAY_MARKERS)
    }

    @Test
    fun `the contract json reports the shell and capabilities`() {
        val json = BridgeContract.contractJson(7, "1.1.0", ready = true)
        assertTrue(json.contains("\"version\":2"))
        assertTrue(json.contains("\"ready\":true"))
        assertTrue(json.contains("\"versionCode\":7"))
        assertTrue(json.contains("\"versionName\":\"1.1.0\""))
        assertTrue(json.contains("\"capabilities\":[\"overlay\",\"overlayRegion\""))
        assertTrue(json.startsWith("{"))
        assertTrue(json.endsWith("}"))
    }

    @Test
    fun `version names are escaped`() {
        val json = BridgeContract.contractJson(1, "bad\"name\\x", ready = false)
        assertTrue(json.contains("bad\\\"name\\\\x"))
        assertTrue(json.contains("\"ready\":false"))
    }

    @Test
    fun `the untrusted payload reveals nothing`() {
        val json = BridgeContract.untrustedJson()
        assertTrue(json.contains("\"version\":0"))
        assertTrue(json.contains("\"ready\":false"))
        assertTrue(json.contains("\"capabilities\":[]"))
        assertFalse(json.contains("shell"))
    }
}
