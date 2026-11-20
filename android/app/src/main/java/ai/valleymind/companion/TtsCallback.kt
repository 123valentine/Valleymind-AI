package ai.valleymind.companion

object TtsCallback {

    private val VALID_NAME = Regex("^vmTts[0-9]{1,9}$")

    fun isValid(name: String?): Boolean = name != null && VALID_NAME.matches(name)

    fun doneJs(name: String, ok: Boolean): String? {
        if (!isValid(name)) return null
        val function = "__vmTtsDone_$name"
        return "window.$function && window.$function($ok);"
    }
}
