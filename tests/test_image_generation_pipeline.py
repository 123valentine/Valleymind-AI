"""Image failover chain, identity-preserving edits and consultative gate tests."""

import base64
import subprocess
from pathlib import Path

import pytest
import requests

from core import brain, image_gen
from core.brain import needs_reference_edit_clarification, wants_reference_image_edit
from core.failover import GENERIC_PROVIDER_ERROR
from core.provider_manager import Capability, ProviderManager


TINY_REFERENCE = base64.b64encode(b"reference-image-bytes").decode("ascii")
PNG_BYTES = b"\x89PNG\r\n\x1a\nimage-bytes"
GOOGLE_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY")


class FakeResponse:
    def __init__(self, status_code=200, content=b"", text="", headers=None, json_data=None):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.headers = headers or {"content-type": "image/png"}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("Response body is not JSON")
        return self._json


class FakeInferenceError(Exception):
    """Stand-in for InferenceClient HTTP/credit failures."""

    def __init__(self, status_code, text="quota exhausted"):
        self.status_code = status_code
        super().__init__(f"Inference HTTP {status_code}: {text}")


def _gemini_payload():
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"inlineData": {"mimeType": "image/png", "data": encoded}}]
                }
            }
        ]
    }


def _disable_google(monkeypatch):
    for key in GOOGLE_KEYS:
        monkeypatch.delenv(key, raising=False)


def _response(spec, ok_content=b"", ok_payload=None):
    if spec.get("timeout"):
        raise requests.exceptions.Timeout("upstream timed out")
    status = spec.get("status", 200)
    text = spec.get("text", "rate limited")
    return FakeResponse(
        status_code=status,
        content=ok_content if status == 200 else b"",
        text=text,
        json_data=ok_payload if status == 200 else None,
    )


def _mock_chain(monkeypatch, gemini=None, hf=None, zerogpu=None, pollinations=None):
    """Patch the full chain and return captured requests.

    ``hf`` and ``zerogpu`` are outcome specs for the new Router/ZeroGPU seams;
    Gemini and Pollinations remain HTTP-level so the outer chain is exercised.
    """
    captured = {"gemini": None, "hf": None, "zerogpu": None, "pollinations": None}
    gemini = gemini or {}
    hf = hf or {}
    zerogpu = zerogpu or {}
    pollinations = pollinations or {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        if "generativelanguage.googleapis.com" not in url:
            raise AssertionError(f"Unexpected POST to {url}")
        captured["gemini"] = {"url": url, "headers": headers, "payload": json}
        return _response(gemini, ok_payload=_gemini_payload())

    def fake_inference(prompt, image_bytes=None, size=(1024, 1024), model_id=None):
        captured["hf"] = {
            "prompt": prompt,
            "image_bytes": image_bytes,
            "size": size,
            "model_id": model_id,
        }
        response = _response(hf, ok_content=PNG_BYTES + b"-hf")
        if response.status_code != 200:
            raise FakeInferenceError(response.status_code, response.text)
        return response.content

    def fake_zerogpu(prompt, image_bytes, size=(1024, 1024)):
        captured["zerogpu"] = {
            "prompt": prompt,
            "image_bytes": image_bytes,
            "size": size,
        }
        response = _response(zerogpu, ok_content=PNG_BYTES + b"-zerogpu")
        if response.status_code != 200:
            raise FakeInferenceError(response.status_code, response.text)
        return response.content

    def fake_get(url, params=None, headers=None, timeout=None, **kwargs):
        captured["pollinations"] = {"url": url, "params": params or {}, "headers": headers}
        return _response(pollinations, ok_content=PNG_BYTES + b"-backup")

    monkeypatch.setattr(image_gen.requests, "post", fake_post)
    monkeypatch.setattr(image_gen.requests, "get", fake_get)
    monkeypatch.setattr(image_gen, "_call_inference_image", fake_inference)
    monkeypatch.setattr(image_gen, "_call_zerogpu_image", fake_zerogpu)
    monkeypatch.setattr(image_gen, "_save_image", lambda data, ext: "/static/generated/result.png")
    monkeypatch.setattr(
        image_gen, "_host_reference_image",
        lambda data, mime: "https://references.example/ref-1.png",
    )
    return captured


def _image_manager():
    manager = ProviderManager()
    manager._providers[Capability.IMAGE] = [
        p for p in manager._providers[Capability.IMAGE]
        if p.name in ("Gemini", "HuggingFace", "Pollinations")
    ]
    return manager


def test_gemini_is_first_and_clean_copy_has_no_provider_names(monkeypatch):
    captured = _mock_chain(monkeypatch)

    result = image_gen.generate_image(
        "A Gemini-styled Flux poster", api_key="test-google-key", enhance=False
    )

    assert image_gen.GEMINI_IMAGE_MODEL in captured["gemini"]["url"]
    assert captured["gemini"]["headers"]["x-goog-api-key"] == "test-google-key"
    assert captured["hf"] is None
    assert captured["pollinations"] is None
    assert result["image_url"] == "/static/generated/result.png"
    for name in ("gemini", "flux", "hugging", "pollinations"):
        assert name not in result["revised_prompt"].lower()


def test_gemini_reference_edit_sends_inline_reference_with_guardrail(monkeypatch):
    captured = _mock_chain(monkeypatch)

    result = image_gen.generate_image(
        "Change my outfit to a red dress",
        api_key="test-google-key",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )

    parts = captured["gemini"]["payload"]["contents"][0]["parts"]
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == b"reference-image-bytes"
    sent_prompt = parts[0]["text"]
    for phrase in ("exact facial identity", "natural skin texture", "facial structure", "likeness"):
        assert phrase in sent_prompt
    assert "Change only the requested outfit" in sent_prompt
    assert result["revised_prompt"] == "Change my outfit to a red dress"
    assert result["mode"] == "image_to_image"


@pytest.mark.parametrize("failure", [{"status": 402}, {"status": 429}, {"timeout": True}])
def test_gemini_failure_falls_back_to_hf_then_pollinations(monkeypatch, failure):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    captured = _mock_chain(monkeypatch, gemini=failure, hf={"status": 402})

    result = image_gen.generate_image(
        "A lighthouse at dawn", api_key="test-google-key", enhance=False
    )

    assert captured["hf"] is not None
    assert captured["hf"]["model_id"] is None  # default Router text model
    assert captured["zerogpu"] is None  # text generation never needs a ZeroGPU edit
    assert captured["pollinations"]["params"]["model"] == "flux"
    assert result["image_url"] == "/static/generated/result.png"


@pytest.mark.parametrize("failure", [{"status": 402}, {"status": 429}, {"timeout": True}])
def test_hf_failure_falls_back_to_pollinations(monkeypatch, failure):
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    captured = _mock_chain(monkeypatch, hf=failure)

    result = image_gen.generate_image("A quiet garden", enhance=False)

    assert captured["gemini"] is None
    assert captured["hf"] is not None
    assert captured["zerogpu"] is None
    assert captured["pollinations"]["params"]["model"] == "flux"
    assert result["image_url"] == "/static/generated/result.png"


def test_hf_kontext_edit_and_schnell_text_when_google_key_absent(monkeypatch):
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    captured = _mock_chain(monkeypatch)

    edit = image_gen.generate_image(
        "Change the background to a studio",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )
    assert captured["hf"]["image_bytes"] == b"reference-image-bytes"
    assert "exact facial identity" in captured["hf"]["prompt"]
    assert captured["zerogpu"] is None
    assert edit["mode"] == "image_to_image"

    text = image_gen.generate_image("A lighthouse at dawn", enhance=False)
    assert captured["hf"]["image_bytes"] is None
    assert captured["hf"]["model_id"] is None
    assert text["mode"] == "text_to_image"


@pytest.mark.parametrize("zerogpu_failure", [None, {"status": 402, "text": "quota exhausted"}])
def test_router_credit_edit_fails_over_to_zerogpu_then_authenticated_backup(monkeypatch, zerogpu_failure):
    """Router 402 -> ZeroGPU gradio_client -> authenticated kontext -> done."""
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setenv("POLLINATIONS_API_KEY", "test-backup-key")
    captured = _mock_chain(
        monkeypatch,
        hf={"status": 402, "text": "payment required"},
        zerogpu=zerogpu_failure,
    )

    result = image_gen.generate_image(
        "Change my outfit to a red dress",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )

    assert captured["hf"]["image_bytes"] == b"reference-image-bytes"
    assert captured["zerogpu"] is not None
    assert captured["zerogpu"]["image_bytes"] == b"reference-image-bytes"
    assert "exact facial identity" in captured["zerogpu"]["prompt"]
    if zerogpu_failure:
        assert captured["pollinations"]["params"]["model"] == "kontext"
        assert captured["pollinations"]["url"].startswith(image_gen.POLLINATIONS_GEN_BASE)
        assert captured["pollinations"]["headers"]["Authorization"] == "Bearer test-backup-key"
    else:
        assert captured["pollinations"] is None
    assert result["mode"] == "image_to_image"


def test_text_only_model_never_replaces_reference_photo(monkeypatch):
    """A flux/schnell request with a photo is refused, not silently regenerated."""
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setenv("POLLINATIONS_API_KEY", "test-backup-key")
    captured = _mock_chain(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        image_gen.generate_image(
            "Change my outfit to a red dress",
            model="flux",
            enhance=False,
            reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
        )

    assert str(exc.value) == image_gen.REFERENCE_EDIT_RETRY_MESSAGE
    assert captured["gemini"] is None
    assert captured["hf"] is None
    assert captured["pollinations"] is None


def test_kontext_model_id_is_allowed_for_reference_edit(monkeypatch):
    """Kontext ids contain 'flux' but still preserve identity."""
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.delenv("POLLINATIONS_API_KEY", raising=False)
    captured = _mock_chain(monkeypatch)

    result = image_gen.generate_image(
        "Change the background to a studio",
        model="black-forest-labs/FLUX.1-Kontext-dev",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )

    assert captured["hf"]["model_id"] == "black-forest-labs/FLUX.1-Kontext-dev"
    assert captured["hf"]["image_bytes"] == b"reference-image-bytes"
    assert "exact facial identity" in captured["hf"]["prompt"]
    assert result["mode"] == "image_to_image"


def test_busy_edit_chain_never_uses_keyless_text_fallback(monkeypatch):
    """When every identity-preserving path is busy, return clean retry copy."""
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.delenv("POLLINATIONS_API_KEY", raising=False)
    captured = _mock_chain(
        monkeypatch,
        hf={"status": 402, "text": "payment required"},
        zerogpu={"status": 429, "text": "rate limited"},
    )

    with pytest.raises(RuntimeError) as exc:
        image_gen.generate_image(
            "Change my outfit to a red dress",
            enhance=False,
            reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
        )

    message = str(exc.value)
    assert message == image_gen.REFERENCE_EDIT_RETRY_MESSAGE
    assert "photo is still attached" in message
    for name in ("gemini", "hugging", "pollinations", "flux", "429", "402"):
        assert name not in message.lower()
    assert captured["pollinations"] is None
    assert captured["zerogpu"] is not None


def test_persist_chat_message_keeps_uploaded_reference(monkeypatch):
    from app import _persist_chat_message

    calls = []

    class Memory:
        def add_message(self, chat_id, role, content, **kwargs):
            calls.append((chat_id, role, content, kwargs))

    class Profile:
        key = "main"

    class Marcus:
        profile = Profile()
        memory = Memory()

    monkeypatch.setattr("app.load_marcus", lambda user_id: Marcus())
    data_uri = "data:image/png;base64," + TINY_REFERENCE

    _persist_chat_message("user-1", "chat-1", "user", "change my outfit", image_data=data_uri)

    assert len(calls) == 1
    assert calls[0][:3] == ("chat-1", "user", "change my outfit")
    assert calls[0][3]["image_data"] == data_uri


def test_pollinations_uses_authenticated_endpoint_when_key_present(monkeypatch):
    _disable_google(monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("POLLINATIONS_API_KEY", "test-backup-key")
    captured = _mock_chain(monkeypatch)

    image_gen.generate_image("A lighthouse at dawn", enhance=False)

    assert captured["pollinations"]["url"].startswith(image_gen.POLLINATIONS_GEN_BASE)
    assert captured["pollinations"]["headers"]["Authorization"] == "Bearer test-backup-key"


def test_exhausted_chain_raises_generic_error_only(monkeypatch):
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    _mock_chain(
        monkeypatch,
        hf={"status": 402, "text": "HF quota exhausted"},
        pollinations={"status": 429, "text": "Pollinations rate limit"},
    )

    with pytest.raises(RuntimeError) as exc:
        image_gen.generate_image("A lighthouse at dawn", enhance=False)

    message = str(exc.value)
    assert message == GENERIC_PROVIDER_ERROR
    for name in ("hugging", "pollinations", "gemini", "quota", "429"):
        assert name not in message.lower()


def test_provider_manager_starts_with_gemini_then_falls_back(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    captured = _mock_chain(monkeypatch)

    result = _image_manager().execute(
        Capability.IMAGE,
        prompt="A sunrise over the sea",
        api_key="test-google-key",
        enhance=False,
    )
    assert result.success
    assert result.provider_name == "Gemini"
    assert image_gen.GEMINI_IMAGE_MODEL in captured["gemini"]["url"]

    captured = _mock_chain(monkeypatch, gemini={"status": 402})
    result = _image_manager().execute(
        Capability.IMAGE,
        prompt="Change my outfit to a blue jacket",
        api_key="test-google-key",
        enhance=False,
        reference_image={"data": TINY_REFERENCE, "mimeType": "image/png"},
    )
    assert result.success
    assert result.provider_name == "HuggingFace"
    assert captured["hf"]["image_bytes"] == b"reference-image-bytes"
    assert "exact facial identity" in captured["hf"]["prompt"]


def test_provider_manager_passes_hf_model_hint(monkeypatch):
    _disable_google(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    captured = _mock_chain(monkeypatch)

    result = _image_manager().execute(
        Capability.IMAGE,
        prompt="A sunrise over the sea",
        enhance=False,
        model="hf:custom-model",
    )

    assert result.success
    assert captured["hf"]["model_id"] == "custom-model"
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
    from app import _chat_reference_image, _prepare_asset_request

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


def test_privacy_guard_scrubs_all_generation_provider_names():
    copy = brain._privacy_guard(
        "Gemini, Google AI Studio, Hugging Face, HuggingFace, Pollinations, Qwen and Alibaba "
        "are only infrastructure details."
    )
    for name in ("gemini", "google", "hugging", "pollinations", "qwen", "alibaba"):
        assert name not in copy.lower()


def test_no_deprecated_api_inference_endpoint_remains():
    """The retired host must be absent from every tracked file."""
    retired_host = "api-" + "inference.huggingface.co"
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "grep", "-n", "-i", retired_host],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout
    assert result.stdout.strip() == ""
