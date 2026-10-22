package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The floating companion's drag behaviour, on the JVM.
 *
 * Two rules are locked in here because they are the difference between
 * "dragging feels broken" and "dragging feels right":
 *
 *   1. a gesture that never passes the platform touch slop is a TAP — it must
 *      not move the window, because a tap is what wakes the companion;
 *   2. a gesture that does pass the slop is a DRAG — it moves the window, and
 *      it is always clamped so the companion cannot be dragged off-screen and
 *      lost.
 */
class OverlayDragTrackerTest {

    private val slop = 24

    private fun bounds(x: Int = 0, y: Int = 0) = OverlayBounds(x, 1080, y, 1920)

    @Test
    fun `a gesture inside the slop is a tap and never moves the window`() {
        val t = OverlayDragTracker(slop)
        t.onDown(100f, 200f, 40, 60)

        assertNull("a move inside the slop must not move the window", t.onMove(110f, 205f, bounds()))
        assertNull("a move at the slop edge must not move the window", t.onMove(124f, 200f, bounds()))

        assertTrue("releasing without crossing the slop is a tap", t.onUp())
    }

    @Test
    fun `a gesture past the slop is a drag`() {
        val t = OverlayDragTracker(slop)
        t.onDown(100f, 200f, 40, 60)

        val move = t.onMove(140f, 200f, bounds())

        assertTrue("crossing the slop starts the drag", t.dragging)
        assertEquals(80, move!!.x)
        assertEquals(60, move.y)
        assertFalse("releasing a drag is not a tap", t.onUp())
    }

    @Test
    fun `the drag follows the finger from the window's original position`() {
        val t = OverlayDragTracker(slop)
        t.onDown(500f, 500f, 200, 300)

        val move = t.onMove(560f, 470f, bounds())

        assertEquals(260, move!!.x)
        assertEquals(270, move.y)
    }

    @Test
    fun `the window can never be dragged off-screen`() {
        val t = OverlayDragTracker(slop)
        t.onDown(100f, 100f, 1000, 1800)

        val move = t.onMove(5000f, 5000f, bounds())

        assertEquals("the x position is clamped to the screen", 1080, move!!.x)
        assertEquals("the y position is clamped to the screen", 1920, move.y)
    }

    @Test
    fun `the window can never be dragged into the top-left corner`() {
        val t = OverlayDragTracker(slop)
        t.onDown(900f, 900f, 500, 500)

        val move = t.onMove(-500f, -500f, bounds())

        assertEquals(0, move!!.x)
        assertEquals(0, move.y)
    }

    @Test
    fun `a cancelled gesture is never a tap`() {
        val t = OverlayDragTracker(slop)
        t.onDown(100f, 200f, 40, 60)
        t.cancel()

        assertFalse("a cancelled gesture does not wake the companion", t.onUp())
    }

    @Test
    fun `a new gesture starts from the current window position`() {
        val t = OverlayDragTracker(slop)
        t.onDown(100f, 100f, 0, 0)
        t.onMove(200f, 100f, bounds())
        t.onUp()

        t.onDown(200f, 100f, 100, 0)
        val move = t.onMove(300f, 100f, bounds())

        assertEquals(200, move!!.x)
        assertEquals(0, move.y)
    }

    @Test
    fun `bounds clamp to a valid range even on a screen smaller than the window`() {
        val tiny = OverlayBounds(0, 0, 0, 0)
        assertEquals(0, tiny.clampX(500))
        assertEquals(0, tiny.clampY(-20))
    }
}
