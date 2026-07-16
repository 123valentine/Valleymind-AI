# Valleymind AI — capabilities registry
# Update convention: when adding/removing/modifying capabilities, update
# this list. Then check both _groq_messages() and
# _build_multimodal_system_prompt() in brain.py to ensure the change is
# reflected in Marcus's system prompt.

CAPABILITIES = [
    {"name": "Text chat", "status": "live", "description": "Conversational chat with memory and streaming responses"},
    {"name": "Long-term memory", "status": "live", "description": "Remembers facts, preferences, projects, and tentative thoughts across conversations"},
    {"name": "Google sign-in", "status": "live", "description": "Login via Google account"},
    {"name": "Email/password auth", "status": "live", "description": "Login, password reset, and password change"},
    {"name": "Chat sessions", "status": "live", "description": "Create, rename, delete, and switch between chat threads"},
    {"name": "Message reactions", "status": "live", "description": "Thumbs up/down feedback on messages"},
    {"name": "Image upload in chat", "status": "live", "description": "Attach and discuss images directly in conversation"},
    {"name": "Suggestion box", "status": "live", "description": "Users can submit feature suggestions"},
    {"name": "Image generation", "status": "live", "description": "Create images from text descriptions"},
    {"name": "Video generation", "status": "coming_soon", "description": "Not yet built"},
    {"name": "3D model generation", "status": "coming_soon", "description": "Not yet built"},
    {"name": "Website builder", "status": "coming_soon", "description": "Not yet built"},
    {"name": "Google Drive integration", "status": "coming_soon", "description": "Not yet built"},
    {"name": "Website URL import", "status": "coming_soon", "description": "Not yet built"},
    {"name": "Regenerate response", "status": "coming_soon", "description": "Not yet built"},
    {"name": "Sketch tool", "status": "broken", "description": "UI exists but is non-functional — do not tell users this works"},
    {"name": "Voice/microphone input", "status": "broken", "description": "UI exists but is non-functional — do not tell users this works"},
]


def build_capabilities_prompt() -> str:
    live = [c["name"] for c in CAPABILITIES if c["status"] == "live"]
    coming = [c["name"] for c in CAPABILITIES if c["status"] == "coming_soon"]
    broken = [c["name"] for c in CAPABILITIES if c["status"] == "broken"]

    lines = ["\n---\n## YOUR CURRENT ACTUAL CAPABILITIES\n"]
    lines.append("LIVE (fully working, tell users confidently): " + ", ".join(live))
    lines.append("")
    lines.append("COMING SOON (tell users this is planned but not ready yet): " + ", ".join(coming))
    lines.append("")
    lines.append("KNOWN ISSUES (these exist in the UI but don't work — if a user mentions trying sketch or voice "
                 "and it not working, acknowledge it's a known issue being fixed, don't pretend it should work): "
                 + ", ".join(broken))
    lines.append("")
    return "\n".join(lines)
