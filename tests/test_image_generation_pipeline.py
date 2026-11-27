"""Free-cloud image generation, reference editing and consultative gate tests."""

import base64
from urllib.parse import parse_qs, unquote, urlsplit

from core import brain, image_gen
from core.brain import needs_reference_edit_clarification, wants_reference_image_edit
from core.provider_manager import Capability, ProviderManager


TINY_REFERENCE = base64.b64encode(b"reference-image-bytes").decode("ascii")


class FakeResponse:
    def __init__(self, status_code=200, content=b"imagebytes", text="", headers=None):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.headers = headers or {"content-type": "image/png"}


def _capture_hf(monkeypatch, status_code=200):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return FakeResponse(
            status_code=status_code,
            content=b"" if status_code != 200 else b"\x89PNG\r\n\x1a\nedited",
            text="rate limited" if status_code != 200 else "",
        )

    monkeypatch.setattr(image_gen.requests, "post", fake_post)
    return captured


def _capture_pollinations(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params or {}, timeout=timeout)
        return FakeResponse(content=b"\x89PNG\r\n\x1a\ngenerated")

    monkeypatch.setattr(image_gen.requests, "get", fake_get)
    return captured


def test_reference_edit_uses_kontext_and_injects_identity_guardrail(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/edit.png")
    captured = _capture_hf(monkeypatch)

    result = image_gen.generate_image(
        "Change my outfit to a red dress",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )

    assert image_gen.HF_KONTEXT_MODEL in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer hf_test_token"
    assert base64.b64decode(captured["payload"]["inputs"]) == b"reference-image-bytes"
    sent_prompt = captured["payload"]["parameters"]["prompt"]
    for phrase in ("exact facial identity", "natural skin texture", "facial structure", "likeness"):
        assert phrase in sent_prompt
    assert "artificially smooth" in sent_prompt
    assert "Change only the requested outfit" in sent_prompt
    # User-facing copy must never carry internal guardrail text.
    assert result["revised_prompt"] == "Change my outfit to a red dress"
    assert "exact facial identity" not in result["revised_prompt"]
    assert result["mode"] == "image_to_image"
    assert result["image_url"] == "/static/generated/edit.png"


def test_text_only_image_uses_schnell(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/text.png")
    captured = _capture_hf(monkeypatch)

    result = image_gen.generate_image("A lighthouse at dawn", enhance=False)

    assert image_gen.HF_SCHNELL_MODEL in captured["url"]
    assert captured["payload"]["inputs"] == "A lighthouse at dawn"
    assert result["mode"] == "text_to_image"


def test_missing_token_falls_back_to_free_text_to_image(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/flux.png")
    captured = _capture_pollinations(monkeypatch)

    result = image_gen.generate_image("A lighthouse at dawn", enhance=False)

    assert captured["params"]["model"] == "flux"
    assert captured["params"]["width"] == 1024
    assert result["image_url"] == "/static/generated/flux.png"


def test_missing_token_falls_back_to_hosted_reference_edit(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/kontext.png")
    monkeypatch.setattr(
        image_gen, "_host_reference_image",
        lambda data, mime: "https://references.example/ref-1.png",
    )
    captured = _capture_pollinations(monkeypatch)

    result = image_gen.generate_image(
        "Change the background to a studio",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )

    assert captured["params"]["model"] == "kontext"
    assert captured["params"]["image"] == "https://references.example/ref-1.png"
    assert "exact facial identity" in unquote(captured["url"])
    assert result["mode"] == "image_to_image"


def test_hf_rate_limit_falls_back_automatically(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/fallback.png")
    _capture_hf(monkeypatch, status_code=429)
    captured = _capture_pollinations(monkeypatch)

    result = image_gen.generate_image("A quiet garden", enhance=False)

    assert captured["params"]["model"] == "flux"
    assert result["image_url"] == "/static/generated/fallback.png"


def test_provider_manager_routes_reference_request_to_primary_first(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/manager.png")
    captured = _capture_hf(monkeypatch)
    _capture_pollinations(monkeypatch)

    result = ProviderManager().execute(
        Capability.IMAGE,
        prompt="Change my outfit to a blue jacket",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )

    assert result.success
    assert result.provider_name == "HuggingFace"
    assert image_gen.HF_KONTEXT_MODEL in captured["url"]
    assert "exact facial identity" in captured["payload"]["parameters"]["prompt"]


def test_provider_manager_passes_model_hint_to_primary(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/model.png")
    captured = _capture_hf(monkeypatch)

    result = ProviderManager().execute(
        Capability.IMAGE,
        prompt="A sunrise over the sea",
        enhance=False,
        model="hf:custom-model",
    )

    assert result.success
    assert captured["url"].endswith("/custom-model")
    assert result.provider_name == "HuggingFace"


def test_reference_edit_detection_and_consultation():
    assert wants_reference_image_edit("change my outfit in this photo", True)
    assert wants_reference_image_edit("use this photo as a reference and restyle me", True)
    assert not wants_reference_image_edit("change my outfit", False)
    assert not wants_reference_image_edit("what is in this photo?", True)

    assert needs_reference_edit_clarification("change my outfit")
    assert needs_reference_edit_clarification("restyle me")
    assert not needs_reference_edit_clarification("change my outfit to a red dress, create it now")
    assert not needs_reference_edit_clarification(
        "change my outfit to a red dress, cinematic studio lighting, city background"
    )


def test_chat_consultative_gate_keeps_identity_copy_and_confirmation(monkeypatch):
    from app import _chat_reference_image, _image_reply_copy, _prepare_asset_request

    brain._pending_asset_requests.clear()
    monkeypatch.setattr("app._asset_chat_history", lambda user_id, chat_id: [])
    reference = {"data": TINY_REFERENCE, "mimeType": "image/png"}

    prepared, clarification = _prepare_asset_request(
        "user-1", "chat-1", "change my outfit", "image", reference_image=reference
    )
    assert prepared is None
    assert "Quick questions before I create" in clarification
    assert "exact facial identity" in clarification
    lowered = clarification.lower()
    assert "what outfit or clothing" in lowered
    assert "style or vibe" in lowered
    assert "background or setting" in lowered

    prepared, clarification = _prepare_asset_request(
        "user-1", "chat-1", "create it now", "image", reference_image=reference
    )
    assert clarification is None
    assert "create it now" in prepared

    detected = _chat_reference_image("Change my clothes in this photo", "data:image/png;base64," + TINY_REFERENCE)
    assert detected["mimeType"] == "image/png"
    assert detected["data"].endswith(TINY_REFERENCE)


def test_generated_image_reply_has_card_and_image_followups():
    from app import _image_reply_copy

    copy = _image_reply_copy(
        "change my outfit", "/static/generated/result.png", "A red dress", reference=True
    )
    assert "[[IMAGE_CARD: /static/generated/result.png | A red dress | valleymind_image.png]]" in copy
    assert "exact facial identity" in copy
    assert "lighting" in copy.lower()
    assert "background" in copy.lower()
    assert "aspect ratio" in copy.lower()
    assert "comparison table" not in copy.lower()
