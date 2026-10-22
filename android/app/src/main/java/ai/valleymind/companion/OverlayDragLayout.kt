package ai.valleymind.companion

import android.content.Context
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.widget.LinearLayout

/**
 * OverlayDragLayout — the floating companion window's touch surface.
 *
 * The companion is a character on a transparent window, so the whole window is
 * the drag target; there is no separate "handle" strip the user has to find
 * first (that was the old behaviour and it is why dragging felt broken: the
 * visible avatar did not move, a thin bar at the top did).
 *
 * TAP vs DRAG is decided by the platform touch slop and enforced structurally:
 *
 *   - While the gesture is still within the slop, onInterceptTouchEvent
 *     returns false, so the WebView keeps the gesture and a plain tap produces
 *     a normal DOM click on the character (that is what wakes the companion).
 *   - The moment the finger passes the slop, the layout intercepts the event.
 *     Android then sends ACTION_CANCEL to the WebView, so a drag can NEVER
 *     also fire the tap — the character is moved, not woken.
 *   - Once the parent owns the gesture, onTouchEvent drives the drag.
 *
 * The actual movement is delegated to the host (the service), which owns the
 * WindowManager.LayoutParams; this class never touches a WindowManager.
 */
class OverlayDragLayout(context: Context) : LinearLayout(context) {

    interface Host {
        /** Called once when a gesture turns into a drag. */
        fun onDragStart()

        /** Called with the NEW window position (already clamped). */
        fun onDrag(x: Int, y: Int)

        /** Called when the drag ends or is cancelled. */
        fun onDragEnd()
    }

    var host: Host? = null

    /** Current window position in layout-parameter space (the drag origin). */
    var windowX: Int = 0
    var windowY: Int = 0

    /** Supplies the rectangle the window may occupy. */
    var boundsProvider: (() -> OverlayBounds)? = null

    private val touchSlopPx: Int = ViewConfiguration.get(context).scaledTouchSlop
    private val tracker = OverlayDragTracker(touchSlopPx)

    init {
        // The layout itself must never try to scroll; the host handles motion.
        isMotionEventSplittingEnabled = false
    }

    /**
     * The WebView asks its parent not to intercept once it thinks a gesture is a
     * scroll. The overlay page never scrolls (overflow:hidden, touch-action:none),
     * so honouring that request would make the companion impossible to drag on
     * some WebView builds. The whole window is the drag surface.
     */
    override fun requestDisallowInterceptTouchEvent(disallowIntercept: Boolean) {
        // deliberately ignored
    }

    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean {
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                tracker.onDown(ev.rawX, ev.rawY, windowX, windowY)
                // Let the WebView own the gesture until it becomes a drag.
                return false
            }
            MotionEvent.ACTION_MOVE -> {
                val move = tracker.onMove(ev.rawX, ev.rawY, boundsProvider?.invoke() ?: OverlayBounds.empty())
                if (move != null) {
                    // The gesture is a drag: take it over. The WebView will get
                    // ACTION_CANCEL, so no tap is synthesised from the drag.
                    host?.onDragStart()
                    host?.onDrag(move.x, move.y)
                    return true
                }
                return false
            }
            MotionEvent.ACTION_UP -> {
                tracker.onUp()
                return false
            }
            MotionEvent.ACTION_CANCEL -> {
                tracker.cancel()
                return false
            }
        }
        return false
    }

    override fun onTouchEvent(ev: MotionEvent): Boolean {
        when (ev.actionMasked) {
            MotionEvent.ACTION_MOVE -> {
                val move = tracker.onMove(ev.rawX, ev.rawY, boundsProvider?.invoke() ?: OverlayBounds.empty())
                if (move != null) host?.onDrag(move.x, move.y)
                return true
            }
            MotionEvent.ACTION_UP -> {
                tracker.onUp()
                host?.onDragEnd()
                return true
            }
            MotionEvent.ACTION_CANCEL -> {
                tracker.cancel()
                host?.onDragEnd()
                return true
            }
        }
        return true
    }
}
