package ai.valleymind.companion

import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Validation rules for the overlay's interactive region.
 *
 * The page may ask the native overlay window to become smaller so the
 * character occupies only part of the floating window and the rest can pass
 * touches through to the app underneath. Android can only resize the whole
 * WindowManager window (there is no per-pixel input pass-through), so the page
 * must not be allowed to resize it arbitrarily: values come from JavaScript and
 * are clamped to a safe, usable range before touching LayoutParams.
 */
object OverlayRegionRules {

    const val MIN_WIDTH_DP = 120
    const val MAX_WIDTH_DP = 360
    const val MIN_HEIGHT_DP = 160
    const val MAX_HEIGHT_DP = 560
    const val DEFAULT_WIDTH_DP = 210
    const val DEFAULT_HEIGHT_DP = 300

    data class Region(val widthDp: Int, val heightDp: Int)

    data class Pixels(val widthPx: Int, val heightPx: Int)

    sealed class Decision {
        data class Accepted(val region: Region, val clamped: Boolean) : Decision()
        data class Rejected(val reason: String) : Decision()
    }

    /**
     * Validates JavaScript-supplied dimensions. Non-finite and non-positive
     * values are rejected (they signal a bug or an attack, not a size); finite
     * values outside the supported range are clamped.
     */
    fun decide(widthDp: Double, heightDp: Double): Decision {
        if (!widthDp.isFinite() || !heightDp.isFinite() || widthDp <= 0.0 || heightDp <= 0.0) {
            return Decision.Rejected("invalid-dimensions")
        }
        val wantedW = widthDp.roundToInt()
        val wantedH = heightDp.roundToInt()
        val w = wantedW.coerceIn(MIN_WIDTH_DP, MAX_WIDTH_DP)
        val h = wantedH.coerceIn(MIN_HEIGHT_DP, MAX_HEIGHT_DP)
        return Decision.Accepted(Region(w, h), clamped = w != wantedW || h != wantedH)
    }

    /** Converts dp to pixels and never exceeds the usable screen. */
    fun toPixels(
        region: Region,
        density: Float,
        screenWidthPx: Int,
        screenHeightPx: Int
    ): Pixels {
        val safeDensity = if (density.isFinite() && density > 0f) density else 1f
        val w = (region.widthDp * safeDensity).roundToInt().coerceIn(1, max(1, screenWidthPx))
        val h = (region.heightDp * safeDensity).roundToInt().coerceIn(1, max(1, screenHeightPx))
        return Pixels(w, h)
    }

    fun pixelsToRegion(widthPx: Int, heightPx: Int, density: Float): Region {
        val safeDensity = if (density.isFinite() && density > 0f) density else 1f
        return Region(
            (widthPx / safeDensity).roundToInt().coerceIn(MIN_WIDTH_DP, MAX_WIDTH_DP),
            (heightPx / safeDensity).roundToInt().coerceIn(MIN_HEIGHT_DP, MAX_HEIGHT_DP)
        )
    }
}
