package ai.valleymind.companion

/**
 * Framework-free state machine for ONE WebView page load.
 *
 * It exists so the "never leave the user stuck on a blank/loading screen"
 * guarantee is enforced by logic that can be unit tested on the JVM, instead of
 * only by manually waiting on a phone. MainActivity drives it from the real
 * WebViewClient callbacks.
 *
 * A load is only considered successful once the WebView reports that content
 * actually committed (onPageCommitVisible). A reported error, an uncommitted
 * finish, or the timeout all move it to FAILED so an error screen + Retry are
 * always offered.
 */
class LoadMonitor(val timeoutMs: Long) {

    enum class State { IDLE, LOADING, COMMITTED, FAILED }

    var state: State = State.IDLE
        private set

    var failureReason: String? = null
        private set

    fun onLoadStarted() {
        state = State.LOADING
        failureReason = null
    }

    /** Real, non-error content started rendering. */
    fun onCommitted() {
        if (state == State.LOADING) state = State.COMMITTED
    }

    fun onFailed(reason: String) {
        if (state == State.COMMITTED) return
        state = State.FAILED
        failureReason = reason
    }

    /**
     * Called when the scheduled timeout fires. @return true when the load must
     * now be treated as failed (it was still in flight and never committed).
     */
    fun onTimeoutFired(): Boolean {
        if (state != State.LOADING) return false
        state = State.FAILED
        failureReason = REASON_TIMEOUT
        return true
    }

    fun isFailed(): Boolean = state == State.FAILED

    /** A failed load may be retried; an in-flight one may not. */
    fun canRetry(): Boolean = state == State.FAILED

    fun retry() {
        if (canRetry()) onLoadStarted()
    }

    companion object {
        const val REASON_TIMEOUT = "timeout"
        const val REASON_NETWORK = "network"
        const val REASON_HTTP = "http"
        const val REASON_SSL = "ssl"
        const val REASON_RENDERER_GONE = "renderer-gone"
        const val REASON_UNCOMMITTED = "uncommitted"

        /** Default generous enough for a cold Render start, short enough not to strand the user. */
        const val DEFAULT_TIMEOUT_MS = 30_000L
    }
}
