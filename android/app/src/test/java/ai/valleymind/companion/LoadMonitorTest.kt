package ai.valleymind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Startup-failure behaviour: the app must never sit on a blank/loading screen
 * indefinitely, and the backend must never be reported ready unless it
 * actually served the page.
 */
class LoadMonitorTest {

    private fun monitor() = LoadMonitor(LoadMonitor.DEFAULT_TIMEOUT_MS)

    @Test
    fun `a load starts in the loading state with no reason`() {
        val m = monitor()
        m.onLoadStarted()
        assertEquals(LoadMonitor.State.LOADING, m.state)
        assertNull(m.failureReason)
        assertFalse(m.isFailed())
        assertFalse(m.canRetry())
    }

    @Test
    fun `content committing marks the load successful`() {
        val m = monitor()
        m.onLoadStarted()
        m.onCommitted()
        assertEquals(LoadMonitor.State.COMMITTED, m.state)
        assertFalse(m.isFailed())
    }

    @Test
    fun `a reported network error fails the load and offers a retry`() {
        val m = monitor()
        m.onLoadStarted()
        m.onFailed(LoadMonitor.REASON_NETWORK)
        assertTrue(m.isFailed())
        assertEquals(LoadMonitor.REASON_NETWORK, m.failureReason)
        assertTrue(m.canRetry())
    }

    @Test
    fun `an http error fails the load`() {
        val m = monitor()
        m.onLoadStarted()
        m.onFailed(LoadMonitor.REASON_HTTP)
        assertTrue(m.isFailed())
        assertEquals(LoadMonitor.REASON_HTTP, m.failureReason)
    }

    @Test
    fun `an ssl error fails the load`() {
        val m = monitor()
        m.onLoadStarted()
        m.onFailed(LoadMonitor.REASON_SSL)
        assertTrue(m.isFailed())
    }

    @Test
    fun `a vanished renderer fails the load`() {
        val m = monitor()
        m.onLoadStarted()
        m.onFailed(LoadMonitor.REASON_RENDERER_GONE)
        assertTrue(m.isFailed())
        assertEquals(LoadMonitor.REASON_RENDERER_GONE, m.failureReason)
    }

    @Test
    fun `a timeout while still loading fails the load`() {
        val m = monitor()
        m.onLoadStarted()
        assertTrue(m.onTimeoutFired())
        assertTrue(m.isFailed())
        assertEquals(LoadMonitor.REASON_TIMEOUT, m.failureReason)
    }

    @Test
    fun `a timeout after a successful commit is ignored`() {
        val m = monitor()
        m.onLoadStarted()
        m.onCommitted()
        assertFalse(m.onTimeoutFired())
        assertEquals(LoadMonitor.State.COMMITTED, m.state)
    }

    @Test
    fun `a timeout when idle is ignored`() {
        val m = monitor()
        assertFalse(m.onTimeoutFired())
        assertEquals(LoadMonitor.State.IDLE, m.state)
    }

    @Test
    fun `an error after a successful commit cannot retroactively fail it`() {
        val m = monitor()
        m.onLoadStarted()
        m.onCommitted()
        m.onFailed(LoadMonitor.REASON_NETWORK)
        assertEquals(LoadMonitor.State.COMMITTED, m.state)
        assertFalse(m.isFailed())
    }

    @Test
    fun `retry restarts a failed load`() {
        val m = monitor()
        m.onLoadStarted()
        m.onTimeoutFired()
        assertTrue(m.canRetry())
        m.retry()
        assertEquals(LoadMonitor.State.LOADING, m.state)
        assertNull(m.failureReason)
        assertFalse(m.isFailed())
    }

    @Test
    fun `retry is refused while a load is in flight`() {
        val m = monitor()
        m.onLoadStarted()
        assertFalse(m.canRetry())
        m.retry()
        assertEquals(LoadMonitor.State.LOADING, m.state)
    }

    @Test
    fun `a subsequent load after success starts clean`() {
        val m = monitor()
        m.onLoadStarted()
        m.onCommitted()
        m.onLoadStarted()
        assertEquals(LoadMonitor.State.LOADING, m.state)
        assertNull(m.failureReason)
    }

    @Test
    fun `the default timeout is finite so the user is never stranded`() {
        assertTrue(LoadMonitor.DEFAULT_TIMEOUT_MS > 0)
        assertTrue(LoadMonitor.DEFAULT_TIMEOUT_MS <= 120_000L)
    }
}
