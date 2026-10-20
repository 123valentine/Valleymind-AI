package ai.valleymind.companion

/**
 * Pure decision table for the floating companion's ON/OFF flow, including the
 * Android overlay-permission round trip (the system settings screen does not
 * reliably return a result code, so the real permission is re-checked on
 * return and the activation only continues if it was actually granted).
 */
object OverlayStateMachine {

    enum class Action { NONE, REQUEST_PERMISSION, START, STOP, SHOW_ERROR }

    fun onToggle(requested: Boolean, permissionGranted: Boolean): Action = when {
        !requested -> Action.STOP
        permissionGranted -> Action.START
        else -> Action.REQUEST_PERMISSION
    }

    fun onPermissionReturned(permissionGranted: Boolean, pendingEnable: Boolean): Action =
        if (permissionGranted && pendingEnable) Action.START else Action.NONE

    fun onOverlayLoadFailed(): Action = Action.SHOW_ERROR

    fun onOverlayPageVerified(verified: Boolean): Action =
        if (verified) Action.NONE else Action.SHOW_ERROR

    /** A failed overlay load always fails closed rather than showing the app. */
    fun shouldFailClosedOnLoadFailure(): Boolean = true
}
