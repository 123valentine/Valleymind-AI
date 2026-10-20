package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Overlay ON/OFF behaviour, including the Android overlay-permission round trip
 * (the settings screen does not return a reliable result code, so the real
 * permission is re-checked on return).
 */
class OverlayStateMachineTest {

    @Test
    fun `turning the companion off always stops it`() {
        assertEquals(
            OverlayStateMachine.Action.STOP,
            OverlayStateMachine.onToggle(requested = false, permissionGranted = true)
        )
        assertEquals(
            OverlayStateMachine.Action.STOP,
            OverlayStateMachine.onToggle(requested = false, permissionGranted = false)
        )
    }

    @Test
    fun `turning the companion on without permission requests permission instead of lying`() {
        assertEquals(
            OverlayStateMachine.Action.REQUEST_PERMISSION,
            OverlayStateMachine.onToggle(requested = true, permissionGranted = false)
        )
    }

    @Test
    fun `turning the companion on with permission starts it`() {
        assertEquals(
            OverlayStateMachine.Action.START,
            OverlayStateMachine.onToggle(requested = true, permissionGranted = true)
        )
    }

    @Test
    fun `returning from settings with permission and a pending enable starts the overlay`() {
        assertEquals(
            OverlayStateMachine.Action.START,
            OverlayStateMachine.onPermissionReturned(permissionGranted = true, pendingEnable = true)
        )
    }

    @Test
    fun `returning from settings without permission never starts the overlay`() {
        assertEquals(
            OverlayStateMachine.Action.NONE,
            OverlayStateMachine.onPermissionReturned(permissionGranted = false, pendingEnable = true)
        )
    }

    @Test
    fun `returning without a pending enable does nothing`() {
        assertEquals(
            OverlayStateMachine.Action.NONE,
            OverlayStateMachine.onPermissionReturned(permissionGranted = true, pendingEnable = false)
        )
    }

    @Test
    fun `an overlay load failure shows an error rather than the app`() {
        assertEquals(
            OverlayStateMachine.Action.SHOW_ERROR,
            OverlayStateMachine.onOverlayLoadFailed()
        )
        assertTrue(OverlayStateMachine.shouldFailClosedOnLoadFailure())
    }

    @Test
    fun `an unverified overlay document shows an error`() {
        assertEquals(
            OverlayStateMachine.Action.SHOW_ERROR,
            OverlayStateMachine.onOverlayPageVerified(verified = false)
        )
    }

    @Test
    fun `a verified overlay document needs no corrective action`() {
        assertEquals(
            OverlayStateMachine.Action.NONE,
            OverlayStateMachine.onOverlayPageVerified(verified = true)
        )
        assertFalse(OverlayStateMachine.shouldFailClosedOnLoadFailure() &&
            OverlayStateMachine.onOverlayPageVerified(verified = true) == OverlayStateMachine.Action.SHOW_ERROR)
    }
}
