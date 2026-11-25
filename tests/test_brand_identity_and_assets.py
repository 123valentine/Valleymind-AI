"""Canonical brand identity, founder privacy and consultative asset tests."""

import io
import json
import re
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from docx import Document as DocxDocument
from pypdf import PdfReader

from core import brain as brain_module
from core import file_gen
from core.brain import (
    MarcusBrain,
    VALLEYMIND_IDENTITY_AND_HISTORY,
    _CHAT_SYSTEM_PROMPT,
    _chat_prompt_for,
    _detect_file_request,
    asset_clarification_message,
    asset_generation_copy,
    needs_asset_clarification,
    valleymind_identity_reply,
)


RICH_DRAFT = """## Executive summary

This **operating brief** gives a practical view of the platform structure and the handoffs that keep delivery predictable. It is written as a *working reference* for a small team, so every recommendation can be reviewed, owned and measured without waiting for another meeting.

## Structure

The platform is organised so that each layer has one clear responsibility and a single owner. That makes changes easier to trace and prevents a small issue in one area from becoming a slow, cross-team debugging exercise.

├── ValleyMind AI
│   ├── Chat personas
│   │   ├── Marcus
│   │   ├── Angelina
│   │   └── Elena
│   └── Creative tools
│       ├── Images
│       └── Videos

| Area | Owner | Review rhythm | Outcome |
|---|---|---|---|
| Chat | Conversation lead | Weekly | Consistent persona voice |
| Images | Creative lead | Weekly | Reliable visual prompts |
| Videos | Media lead | Per launch | Predictable delivery |
| Voice | Platform lead | Monthly | Clear speech quality |
| Automation | Ops lead | Weekly | Fewer manual handoffs |
| Agents | Product lead | Quarterly | Safer staged rollout |

## Risks and controls

The main risk is unclear ownership, which is addressed by the table above. Every manual adjustment should carry a source, a date and an owner before it is used in a decision.

## Key takeaways

- **Clear ownership** is the cheapest control available.
- Weekly reviews make delivery calmer and more predictable.
- The hierarchy above should stay visible to every contributor.
"""


class _FakeMemory:
    user_id = "test-user"

    def __init__(self):
        self.messages = []

    def reload(self):
        return None

    def get_chat(self, chat_id):
        return list(self.messages)

    def get_message_count(self, chat_id):
        return 1

    def add_message(self, chat_id, role, content, timestamp=None, **kwargs):
        self.messages.append({"role": role, "content": content})

    def save_memory(self):
        return None

    def get_active_facts(self):
        return []

    def get_full_memory(self):
        return ""


def _brain():
    brain = object.__new__(MarcusBrain)
    brain.memory = _FakeMemory()
    brain.profile = SimpleNamespace(
        key="marcus", name="Marcus", to_prompt=lambda: ""
    )
    return brain


def _bytes(meta):
    token = parse_qs(urlsplit(meta["download_url"]).query)["token"][0]
    path, _ = file_gen.get_file(meta["file_id"], token)
    return path.read_bytes()


def _card_meta(reply):
    match = re.search(r"\[\[FILE_CARD:[^\]]*?\|\s*(/api/files/download/[^\]\s]+)\]\]", reply)
    assert match, reply
    url = match.group(1)
    file_id = urlsplit(url).path.rsplit("/", 1)[-1]
    token = parse_qs(urlsplit(url).query)["token"][0]
    return file_gen.get_file(file_id, token)[1]


def _patch_draft(monkeypatch, captured=None):
    def fake_llm(messages, timeout=30, max_tokens=0):
        if captured is not None:
            captured.append((messages, max_tokens))
        return RICH_DRAFT, {"groq_used": True, "fallback_used": False, "fallback_source": ""}

    monkeypatch.setattr(brain_module, "_call_llm_cluster", fake_llm)


# ── Canonical identity and founder privacy ──────────────────────────────────


def test_canonical_identity_is_in_every_persona_prompt():
    assert "ValleyMind AI" in VALLEYMIND_IDENTITY_AND_HISTORY
    assert "June 7, 2025" in VALLEYMIND_IDENTITY_AND_HISTORY
    for name in ("Marcus", "Angelina", "Elena"):
        prompt = _chat_prompt_for(SimpleNamespace(name=name))
        assert "VALLEYMIND AI — CANONICAL BRAND" in prompt
        assert "Egbujie Valentine" in prompt
        assert "NEVER disclose any phone number" in prompt
    assert "You are Elena" in _chat_prompt_for(SimpleNamespace(name="Elena"))
    assert "You are Angelina" in _chat_prompt_for(SimpleNamespace(name="Angelina"))


def test_copy_box_prompt_is_explicit_only_and_recipient_aware():
    prompt = _CHAT_SYSTEM_PROMPT
    assert "Ordinary replies" in prompt
    assert "NEVER put them in a bordered copy box" in prompt
    assert "[[COPY_BOX: Reply | ...]]" in prompt
    assert "help me reply to this" in prompt
    assert "WhatsApp/DM/text messages" in prompt
    assert "Never use [[COPY_BOX:]] for ordinary text" in prompt
    assert "keep any advice for the user in plain prose outside the box" in prompt
    # The old blanket rule that boxed every prompt/template/config is gone.
    assert "configurations, and long copyable snippets" not in prompt


def test_identity_reply_carries_full_public_history_without_private_data():
    reply = valleymind_identity_reply()
    for fact in (
        "Africa's Next Generation AI Platform. One AI. Unlimited Possibilities.",
        "valleymind-ai-opms.onrender.com",
        "valley-mind-website-o3l8.onrender.com",
        "June 7, 2025",
        "Egbujie Valentine",
        "solo founder with zero outside investors, grants or corporate funding",
        "named his ChatGPT Elena",
        "GitHub Desktop",
        "VS Code",
        "floating 3D Android companion",
        "contactvalleymind0@gmail.com",
        "businessvalleymind@gmail.com",
        "supportvalleymind@gmail.com",
    ):
        assert fact in reply
    for secret in ("+234", "915", "Groq", "NVIDIA", "Gemini", "OpenRouter", "Alibaba"):
        assert secret not in reply


def test_respond_uses_canonical_history_for_identity_questions():
    brain = _brain()
    reply = brain.respond("Who founded ValleyMind AI and what is its history?", chat_id="identity-1")
    assert "June 7, 2025" in reply
    assert "Egbujie Valentine" in reply
    assert "Elena" in reply
    assert "GitHub Desktop" in reply
    assert "+234" not in reply
    assert brain.last_response_meta["detected_route"] == "valleymind_identity"


def test_phone_request_is_redirected_without_leaking_a_number():
    brain = _brain()
    reply = brain.respond("What is the founder's phone number?", chat_id="identity-2")
    assert "does not share personal phone numbers" in reply
    assert "supportvalleymind@gmail.com" in reply
    assert "+234" not in reply
    assert not re.search(r"\d{3,4}[\s-]*\d{3,4}", reply)


def test_llm_replies_and_streams_are_privacy_scrubbed(monkeypatch):
    leak = json.dumps({
        "reply": "Call +234 915 127 0571 or email support. We run on Groq, NVIDIA and Gemini.",
        "intent": "general",
    })
    monkeypatch.setattr(
        brain_module, "_call_llm_cluster",
        lambda messages, timeout=30: (leak, {"groq_used": True}),
    )
    brain = _brain()
    reply = brain.respond("Say hello", chat_id="privacy-1")
    assert "+234" not in reply
    assert "915" not in reply
    assert "Groq" not in reply
    assert "NVIDIA" not in reply
    assert "Gemini" not in reply
    assert "support" in reply


# ── Consultative asset creation and follow-ups ──────────────────────────────


def test_under_specified_asset_requests_pause_with_two_to_three_questions():
    for kind in ("file", "image", "video"):
        assert needs_asset_clarification(f"make a {kind}", kind)
        message = asset_clarification_message(f"make a {kind}", kind)
        assert "Quick questions before I create" in message
        assert "1." in message and "2." in message and "3." in message
        assert "create it now" in message
    assert not needs_asset_clarification("make an image, create it now", "image")
    assert not needs_asset_clarification("generate now — a 3D companion", "image")
    assert not needs_asset_clarification("make a cinematic 16:9 video with a slow mood and no text", "video")
    assert not needs_asset_clarification(
        "make a document with three sections, a comparison table and a clear audience", "file"
    )


def test_file_brain_asks_first_then_generates_after_confirmation(monkeypatch):
    captured = []
    _patch_draft(monkeypatch, captured)
    brain = _brain()

    clarification = brain.respond("create a PDF about platform structure", chat_id="clarify-1")
    assert "Quick questions before I create" in clarification
    assert "[[FILE_CARD:" not in clarification
    assert brain.last_response_meta["detected_route"] == "asset_clarification"

    generated = brain.respond(
        "yes, include a hierarchy and use three sections", chat_id="clarify-1"
    )
    assert "[[FILE_CARD:" in generated
    assert "Want me to change a section" in generated
    assert "add a comparison table or a tree breakdown" in generated
    assert "platform structure" in " ".join(m[0][-1]["content"] for m in captured)
    assert captured[0][1] >= 2500


def test_asset_generation_copy_exists_for_images_and_videos():
    for kind in ("image", "video"):
        intro, followups = asset_generation_copy(kind, "a brief")
        assert "Here's the" in intro
        assert "?" in followups
        assert len(re.findall(r"\?", followups)) >= 2


def test_vague_image_request_asks_style_setting_and_aspect_ratio():
    message = "Generate an image of the ValleyMind-AI 3D companion."
    assert needs_asset_clarification(message, "image")
    clarification = asset_clarification_message(message, "image")
    lowered = clarification.lower()
    assert "style" in lowered
    assert "setting" in lowered and "pose" in lowered
    assert "aspect ratio" in lowered
    assert "create it now" in lowered


def test_image_followups_are_image_specific_not_document_boilerplate():
    _intro, followups = asset_generation_copy("image", "a 3D companion")
    lowered = followups.lower()
    assert "style" in lowered
    assert "lighting" in lowered
    assert "background" in lowered
    assert "aspect ratio" in lowered
    for forbidden in ("comparison table", "tree breakdown", "another format", "tree"):
        assert forbidden not in lowered
    assert len(re.findall(r"\?", followups)) >= 2


def test_image_reply_embeds_downloadable_image_card():
    from app import _image_reply_copy

    reply = _image_reply_copy(
        "a 3D companion", "/api/images/gen-1.png", "A friendly 3D companion"
    )
    assert "[[IMAGE_CARD: /api/images/gen-1.png | A friendly 3D companion" in reply
    assert "valleymind_image.png" in reply
    assert "comparison table" not in reply.lower()
    assert "tree breakdown" not in reply.lower()


# ── Rich PDF/DOCX rendering ─────────────────────────────────────────────────


CONTENT = """# Structure Report

This **bold term** and *italic phrase* must render cleanly.

## Platform map

├── ValleyMind AI
│   ├── Chat
│   └── Creative tools
└── 3D Android Companion

| Area | Status | Owner |
|---|---|---|
| Chat | Live | Marcus |
| Images | Live | Angelina |
| Videos | Live | Elena |
"""


def test_pdf_renders_table_tree_and_inline_emphasis_without_raw_markers():
    meta = file_gen.generate_file(CONTENT, "pdf", title="Structure Report")
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(_bytes(meta))).pages
    )
    assert "Structure Report" in text
    assert "bold term" in text
    assert "italic phrase" in text
    assert "ValleyMind AI" in text
    assert "3D Android Companion" in text
    assert "Area" in text and "Owner" in text
    assert "**" not in text


def test_docx_renders_styled_table_shaded_tree_and_bold_runs():
    meta = file_gen.generate_file(CONTENT, "word", title="Structure Report")
    document = DocxDocument(io.BytesIO(_bytes(meta)))
    all_text = "\n".join(
        [p.text for p in document.paragraphs]
        + [cell.text for table in document.tables for row in table.rows for cell in row.cells]
    )
    assert "bold term" in all_text
    assert "italic phrase" in all_text
    assert "ValleyMind AI" in all_text
    assert "├──" in all_text
    assert "└──" in all_text
    assert "│" in all_text
    assert "Owner" in all_text
    assert "**" not in all_text
    assert len(document.tables) >= 2

    runs = [run for paragraph in document.paragraphs for run in paragraph.runs]
    assert any("bold term" in run.text and run.bold for run in runs)
    assert any("italic phrase" in run.text and run.italic for run in runs)


def test_plain_exports_strip_markup_and_keep_tree_text():
    txt = _bytes(file_gen.generate_file(CONTENT, "txt", title="Structure Report")).decode("utf-8")
    md = _bytes(file_gen.generate_file(CONTENT, "md", title="Structure Report")).decode("utf-8")
    for text in (txt, md):
        assert "bold term" in text
        assert "italic phrase" in text
        assert "├── ValleyMind AI" in text
        assert "**" not in text


def test_file_detection_still_recognises_clear_requests():
    detected = _detect_file_request("create a PDF with three sections and create it now")
    assert detected is not None
    assert detected["format"] == "pdf"


if __name__ == "__main__":
    pytest.main([__file__])
