package ai.valleymind.companion

import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * OverlayBounds — the rectangle the floating companion window may occupy.
 *
 * Expressed in the SAME coordinate space as WindowManager.LayoutParams.x/.y
 * (the gravity frame of the overlay), so a position can be clamped without
 * knowing whether the platform inset that frame by the status bar.
 */
data class OverlayBounds(
    val minX: Int,
    val maxX: Int,
    val minY: Int,
    val maxY: Int
) {
    fun clampX(x: Int): Int = x.coerceIn(minX, maxX)

    fun clampY(y: Int): Int = y.coerceIn(minY, maxY)

    companion object {
        /** A degenerate but always-safe rectangle (nothing can be dragged). */
        fun empty(): OverlayBounds = OverlayBounds(0, 0, 0, 0)
    }
}

/**
 * OverlayDragTracker — framework-free tap/drag discrimination and position
 * clamping for the floating companion window.
 *
 * It exists so the two rules that make the overlay feel right can be unit
 * tested on the JVM instead of only by dragging on a phone:
 *
 *   1. A TAP is a gesture that never moved further than the system's touch
 *      slop. It must NOT move the window (and, because the parent layout
 *      cancels the child gesture on drag, it must not also wake the companion).
 *   2. A DRAG follows the finger exactly, but the resulting window position is
 *      always clamped to the usable screen area so the companion can never be
 *      dragged out of reach and lost.
 *
 * The tracker only decides. It never touches a WindowManager.
 */
class OverlayDragTracker(private val touchSlopPx: Int) {

    enum class Mode { IDLE, PENDING, DRAGGING }

    /** The window position produced by the most recent drag move. */
    data class Move(val x: Int, val y: Int)

    var mode: Mode = Mode.IDLE
        private set

    /** True once the gesture exceeded the slop and became a real drag. */
    var dragging: Boolean = false
        private set

    private var downRawX = 0f
    private var downRawY = 0f
    private var originX = 0
    private var originY = 0

    /**
     * Starts a new gesture. [windowX]/[windowY] are the window's current
     * position in layout-parameter space; the drag is measured from there so
     * the window never jumps on the first pixel of movement.
     */
    fun onDown(rawX: Float, rawY: Float, windowX: Int, windowY: Int) {
        downRawX = rawX
        downRawY = rawY
        originX = windowX
        originY = windowY
        dragging = false
        mode = Mode.PENDING
    }

    /**
     * Feeds a pointer move. Returns the NEW clamped window position when the
     * gesture has become a drag, or null while the gesture is still a
     * (possible) tap or when it did not actually move the window.
     */
    fun onMove(rawX: Float, rawY: Float, bounds: OverlayBounds): Move? {
        if (mode == Mode.IDLE) return null
        val dx = rawX - downRawX
        val dy = rawY - downRawY
        if (!dragging) {
            if (abs(dx) <= touchSlopPx && abs(dy) <= touchSlopPx) return null
            dragging = true
            mode = Mode.DRAGGING
        }
        val x = bounds.clampX((originX + dx).roundToInt())
        val y = bounds.clampY((originY + dy).roundToInt())
        return Move(x, y)
    }

    /**
     * Ends the gesture. Returns true when it was a TAP (never exceeded the
     * slop) — the caller then lets the tap reach the page, e.g. to wake the
     * companion.
     */
    fun onUp(): Boolean {
        val wasTap = mode == Mode.PENDING
        mode = Mode.IDLE
        dragging = false
        return wasTap
    }

    /** Abandons the gesture (the parent cancelled it). Never a tap. */
    fun cancel() {
        mode = Mode.IDLE
        dragging = false
    }
}
