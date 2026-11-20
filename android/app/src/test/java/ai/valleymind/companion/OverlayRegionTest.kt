package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OverlayRegionTest {

    private fun accepted(width: Double, height: Double): OverlayRegionRules.Decision.Accepted {
        val decision = OverlayRegionRules.decide(width, height)
        assertTrue("expected an accepted decision for $width x $height", decision is OverlayRegionRules.Decision.Accepted)
        return decision as OverlayRegionRules.Decision.Accepted
    }

    @Test
    fun `the default window size is accepted unchanged`() {
        val decision = accepted(210.0, 300.0)
        assertEquals(OverlayRegionRules.Region(210, 300), decision.region)
        assertEquals(false, decision.clamped)
    }

    @Test
    fun `values are rounded to whole dp`() {
        val decision = accepted(210.4, 300.6)
        assertEquals(OverlayRegionRules.Region(210, 301), decision.region)
        assertEquals(false, decision.clamped)
    }

    @Test
    fun `oversized values are clamped instead of trusted`() {
        val decision = accepted(50_000.0, 50_000.0)
        assertEquals(OverlayRegionRules.Region(360, 560), decision.region)
        assertEquals(true, decision.clamped)
    }

    @Test
    fun `tiny values are clamped to the usable minimum`() {
        val decision = accepted(1.0, 1.0)
        assertEquals(OverlayRegionRules.Region(120, 160), decision.region)
        assertEquals(true, decision.clamped)
    }

    @Test
    fun `non-finite and non-positive dimensions are rejected`() {
        for (pair in listOf(
            0.0 to 300.0,
            -1.0 to 300.0,
            210.0 to 0.0,
            210.0 to -1.0,
            Double.NaN to 300.0,
            210.0 to Double.NaN,
            Double.POSITIVE_INFINITY to 300.0,
            210.0 to Double.NEGATIVE_INFINITY,
        )) {
            assertTrue(
                "must reject ${pair.first} x ${pair.second}",
                OverlayRegionRules.decide(pair.first, pair.second) is OverlayRegionRules.Decision.Rejected
            )
        }
    }

    @Test
    fun `dp is converted to pixels with the display density`() {
        val px = OverlayRegionRules.toPixels(
            OverlayRegionRules.Region(210, 300),
            density = 2.0f,
            screenWidthPx = 1080,
            screenHeightPx = 1920
        )
        assertEquals(OverlayRegionRules.Pixels(420, 600), px)
    }

    @Test
    fun `pixel size never exceeds the screen`() {
        val px = OverlayRegionRules.toPixels(
            OverlayRegionRules.Region(360, 560),
            density = 3.0f,
            screenWidthPx = 320,
            screenHeightPx = 480
        )
        assertEquals(OverlayRegionRules.Pixels(320, 480), px)
    }

    @Test
    fun `an invalid density falls back to one`() {
        val px = OverlayRegionRules.toPixels(
            OverlayRegionRules.Region(210, 300),
            density = 0.0f,
            screenWidthPx = 1000,
            screenHeightPx = 1000
        )
        assertEquals(OverlayRegionRules.Pixels(210, 300), px)
    }

    @Test
    fun `pixels are reported back in dp for the bridge`() {
        assertEquals(
            OverlayRegionRules.Region(210, 300),
            OverlayRegionRules.pixelsToRegion(420, 600, density = 2.0f)
        )
    }
}
