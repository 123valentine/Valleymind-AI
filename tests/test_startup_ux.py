"""Startup & chat UX regression tests (static guarantees about the frontend).

These tests lock the behaviour requested in the "Startup & Chat UX Fix":

  1. The circular loading (splash) screen is the FIRST thing shown on load —
     the sign-up page must NOT flash before the splash for a returning /
     authenticated user.
  2. checkSession never auto-restores the last-active conversation; the app
     opens on a fresh, empty chat while previous conversations stay in the
     sidebar for manual selection.
  3. An unsent draft is preserved WITH its conversation when the user creates
     a New Chat or switches threads, and restored when they return to it.
  4. The Preferences setup only auto-opens when the server reports
     "not_started" (a genuinely new user) — never for a completed user.

These are structural checks on index.html since the logic runs client-side.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class StartupUxStaticTestCase(unittest.TestCase):
    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def test_splash_is_visible_by_default_no_auth_flash(self):
        # The brand splash (circular loading screen) must be the default
        # visible layer so the sign-up page never flashes before it for a
        # returning user.
        html = self._index_html()
        idx = html.index('id="brandSplashScreen"')
        snippet = html[idx:idx + 500]
        self.assertIn("background:#020617", snippet)
        self.assertIn("z-index:999999", snippet,
                      "splash sits above the auth screen")
        self.assertIn("display:flex", snippet,
                      "splash must be display:flex by default (loading first)")
        # No display:none (the old hidden default that caused the auth flash).
        self.assertNotIn("display:none", snippet[:snippet.index('flex-direction:column')],
                         "splash default must not be hidden")

    def test_check_session_hides_splash_on_not_authenticated(self):
        # For a genuinely new (unauthenticated) user the splash is removed so
        # the sign-up page becomes reachable.
        html = self._index_html()
        block = html[html.index("function checkSession()"):]
        # The not-auth branch hides the splash.
        self.assertIn('splashNA.style.display = "none"', block,
                      "not-authenticated path must hide the splash to reach sign-up")

    def test_startup_does_not_auto_restore_last_chat(self):
        html = self._index_html()
        block = html[html.index("function checkSession()"):]
        # Never restore the last-active chat on startup.
        self.assertNotIn("loadLastChatId()", block[:block.index("renderSessionListSkeleton")],
                         "checkSession must not auto-restore the last chat")
        # Fresh, empty chat is the startup state.
        self.assertIn('currentChatId = ""', block)
        self.assertIn("messageStore.length = 0", block)

    def test_preferences_setup_only_for_not_started(self):
        html = self._index_html()
        block = html[html.index("function checkSession()"):]
        # Preference setup only opens when the server reports not_started.
        self.assertIn('setup_status === "not_started"', block,
                      "preferences only auto-open for a fresh user")

    def test_draft_preserved_on_new_chat(self):
        html = self._index_html()
        new_chat = html[html.index("async function newChat()"):]
        new_chat = new_chat[:new_chat.index("function openSidebar()")]
        self.assertIn("saveChatDraft(prevChatId)", new_chat,
                      "New Chat must park the old conversation's unsent draft")
        self.assertIn('newInput.value = ""', new_chat,
                      "New Chat starts a fresh empty composer")

    def test_draft_preserved_on_switch_session(self):
        html = self._index_html()
        switch = html[html.index("async function switchSession("):]
        switch = switch[:switch.index("async function deleteSession(")]
        self.assertIn("saveChatDraft(currentChatId)", switch,
                      "switching away parks the draft")
        self.assertIn("restoreChatDraft(chatId)", switch,
                      "returning restores the draft")

    def test_draft_cleared_on_send(self):
        html = self._index_html()
        start = html.index("window.send = async function () {")
        # Scan the send body up to the next top-level function boundary.
        end_marker = html.find("\n    async function ", start)
        if end_marker == -1:
            end_marker = html.find("\nfunction ", start)
        send_block = html[start:end_marker]
        self.assertIn("clearChatDraft(currentChatId)", send_block,
                      "sending a message clears that chat's draft")

    def test_draft_cleaned_up_on_delete(self):
        # Deleting a thread must also delete any unsent draft stored for it so
        # localStorage does not accumulate orphaned drafts.
        html = self._index_html()
        # Count only CALL sites (exclude the function definition itself).
        calls = html.count("clearChatDraft(chatId)") - \
            html.count("function clearChatDraft(chatId)")
        self.assertEqual(calls, 2,
                         "both delete paths must clear the deleted thread's draft")


if __name__ == "__main__":
    unittest.main()
