package ai.valleymind.companion

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.util.Locale

/**
 * CompanionScreenService — on-demand, permission-gated "what is on this screen?"
 * context for phrases like "Where do I tap here?" / "Help me navigate this app".
 *
 * WHY AN ACCESSIBILITY SERVICE
 * Android gives a normal app no way to read another app's UI. An explicitly
 * enabled AccessibilityService can read the active window's view hierarchy,
 * which is exactly the low-bandwidth, privacy-preserving signal the companion
 * needs: labels and control types, never the pixels of the screen. It is OFF by
 * default and the user must enable it in Settings; when it is off every bridge
 * call returns a harmless `ok:false` payload and the companion falls back to
 * its CameraX eyes (frames go to the SAME /api/vision/analyze endpoint).
 *
 * Nothing is uploaded by native code. The JSON snapshot is handed to the web
 * page, which injects it into the current brain turn only.
 */
class CompanionScreenService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        refresh()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Window changes are the useful signal; view-content changes keep the
        // snapshot fresh while the user moves inside one app.
        refresh()
    }

    override fun onInterrupt() { /* the next event refreshes the snapshot */ }

    private fun refresh() {
        val root = rootInActiveWindow ?: return
        val nodes = ArrayList<String>(MAX_NODES)
        collect(root, nodes, 0)
        val packageName = root.packageName?.toString().orEmpty()
        val summary = buildString {
            append("Active app: ").append(packageName.ifEmpty { "unknown" }).append('\n')
            append("Interactive controls (label — type — id):\n")
            if (nodes.isEmpty()) append("(none reported)\n") else nodes.forEach { append("- ").append(it).append('\n') }
        }
        val sb = StringBuilder(512 + summary.length)
        sb.append("{\"ok\":true,\"package\":\"").append(escape(packageName)).append("\"")
        sb.append(",\"summary\":\"").append(escape(summary)).append("\"")
        sb.append(",\"nodes\":[")
        nodes.forEachIndexed { index, node ->
            if (index > 0) sb.append(',')
            sb.append("{\"label\":\"").append(escape(node)).append("\"}")
        }
        sb.append("]}")
        currentJson = sb.toString()
    }

    private fun collect(node: AccessibilityNodeInfo?, out: MutableList<String>, depth: Int) {
        if (node == null || depth > MAX_DEPTH || out.size >= MAX_NODES) return
        val clickable = node.isClickable
        val focusable = node.isFocusable
        val editable = node.isEditable
        val label = firstText(node.contentDescription, node.text, node.viewIdResourceName)
        if (label.isNotEmpty() && (clickable || focusable || editable || isControl(node.className))) {
            val type = node.className?.toString()?.substringAfterLast('.') ?: "View"
            val id = node.viewIdResourceName?.substringAfter('/') ?: ""
            out.add(label.take(80) + " — " + type + (if (id.isEmpty()) "" else " — " + id))
        }
        for (i in 0 until node.childCount) {
            collect(node.getChild(i), out, depth + 1)
        }
    }

    private fun firstText(vararg values: CharSequence?): String {
        for (v in values) {
            val text = v?.toString()?.trim().orEmpty()
            if (text.isNotEmpty()) return text.replace('\n', ' ')
        }
        return ""
    }

    private fun isControl(className: CharSequence?): Boolean {
        val name = className?.toString()?.lowercase(Locale.ROOT) ?: return false
        return name.contains("button") || name.contains("tab") || name.contains("menu") ||
            name.contains("edittext") || name.contains("switch") || name.contains("checkbox") ||
            name.contains("imagebutton") || name.contains("spinner")
    }

    companion object {
        private const val MAX_DEPTH = 14
        private const val MAX_NODES = 60

        @Volatile
        private var currentJson: String = "{\"ok\":false,\"reason\":\"screen-service-not-enabled\"}"

        /** The last UI-tree snapshot, or a safe `ok:false` payload. */
        fun snapshotJson(): String = currentJson

        /** True only when the user actually enabled THIS service in Settings. */
        fun isEnabled(context: Context): Boolean {
            return try {
                val enabled = Settings.Secure.getInt(
                    context.contentResolver, Settings.Secure.ACCESSIBILITY_ENABLED, 0
                ) == 1
                if (!enabled) return false
                val services = Settings.Secure.getString(
                    context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
                ).orEmpty()
                val component = "${context.packageName}/${CompanionScreenService::class.java.name}"
                services.split(':').any { it.equals(component, ignoreCase = true) }
            } catch (e: Exception) {
                false
            }
        }

        /** Deep-links to the system accessibility settings (never enables it silently). */
        fun openSettings(context: Context) {
            try {
                val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
            } catch (e: Exception) { /* no activity on this device */ }
        }

        private fun escape(raw: String): String {
            val sb = StringBuilder(raw.length + 8)
            for (ch in raw) {
                when {
                    ch == '\\' -> sb.append("\\\\")
                    ch == '"' -> sb.append("\\\"")
                    ch == '\n' -> sb.append("\\n")
                    ch == '\r' -> sb.append("\\r")
                    ch == '\t' -> sb.append("\\t")
                    ch < ' ' -> sb.append(String.format(Locale.ROOT, "\\u%04x", ch.code))
                    else -> sb.append(ch)
                }
            }
            return sb.toString()
        }
    }
}
