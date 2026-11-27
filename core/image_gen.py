"""Free cloud image generation and identity-preserving reference editing.

Primary path: Hugging Face Inference Providers (FLUX.1 Kontext for reference
editing, FLUX.1 schnell for text-to-image) when ``HF_TOKEN`` is available.
Zero-key fallback: Pollinations ``kontext`` (image-to-image) or ``flux``
(text-to-image). Provider names never appear in user-facing copy; everything
here is infrastructure and is only logged server-side.
"""

import base64
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

from core.config import PROJECT_ROOT


GENERATED_DIR = PROJECT_ROOT / "static" / "generated"
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
HF_INFERENCE_BASE = os.getenv(
    "HF_INFERENCE_BASE", "https://router.huggingface.co/hf-inference/models"
).rstrip("/")
HF_KONTEXT_MODEL = "black-forest-labs/FLUX.1-Kontext-dev"
HF_SCHNELL_MODEL = "black-forest-labs/FLUX.1-schnell"

# Anonymous, key-free hosting used only to give the no-key image-to-image
# fallback a public HTTP(S) reference URL. Expire links quickly.
TEMP_IMAGE_HOST = os.getenv("VM_TEMP_IMAGE_HOST", "https://0x0.st").rstrip("/")
TEMP_IMAGE_FALLBACK_HOST = "https://litterbox.catbox.moe/resources/internals/api.php"

MAX_REFERENCE_BYTES = 12 * 1024 * 1024

IDENTITY_PRESERVATION_INSTRUCTIONS = (
    "Using the attached photo as the identity reference, preserve the subject's "
    "exact facial identity, natural skin texture, complexion, facial structure "
    "and likeness. Do not artificially smooth, reshape, beautify or change their "
    "face; they must remain recognisably the same person. Change only the "
    "requested outfit, pose, lighting or background."
)


def _ensure_dir():
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def _save_image(data: bytes, ext: str = "png") -> str:
    _ensure_dir()
    if ext not in ("png", "jpeg", "jpg", "webp", "gif"):
        ext = "png"
    filename = f"{uuid.uuid4().hex}_{datetime.now():%Y%m%d%H%M%S}.{ext}"
    filepath = GENERATED_DIR / filename
    filepath.write_bytes(data)
    print(f"[IMAGE GEN] Saved {filepath}")
    return f"/static/generated/{filename}"


def _call_gemini_text(prompt, system_prompt, api_key, model="gemini-2.0-flash", timeout=15) -> str:
    from core.cost_control import provider_blocked, record_error, record_success
    if provider_blocked("gemini"):
        raise RuntimeError("Gemini: free quota unavailable")
    gemini_base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    payload = {
        "contents": [{"parts": [{"text": prompt}], "role": "user"}],
        "system_instruction": {"parts": [{"text": system_prompt}]},
    }
    response = requests.post(
        f"{gemini_base_url}/models/{model}:generateContent?key={api_key}",
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        record_error("gemini", status_code=response.status_code, body=response.text[:200])
        raise RuntimeError(f"Gemini text HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini text returned empty candidates.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    record_success("gemini")
    return text.strip()


def _enhance_prompt(user_prompt: str, api_key: str) -> str:
    if not api_key:
        return user_prompt
    system = (
        "You are an expert prompt engineer for image generation. "
        "Rewrite the user's request into a detailed, vivid image-generation prompt. "
        "Add visual details like lighting, composition, color palette, mood, "
        "and artistic style. Preserve the user's original intent. "
        "Output ONLY the enhanced prompt — no preamble, no explanation, no quotes."
    )
    try:
        result = _call_gemini_text(user_prompt, system, api_key, timeout=15)
        enhanced = result.strip().strip('"').strip("'")
        if enhanced and len(enhanced) > len(user_prompt) * 0.5:
            print(f"[IMAGE GEN] Enhanced prompt: {enhanced[:120]}...")
            return enhanced
    except Exception as exc:
        print(f"[IMAGE GEN] Prompt enhancement failed: {exc}")
    return user_prompt


def _sanitize_prompt(prompt: str) -> str:
    prompt = str(prompt or "").strip()
    prompt = re.sub(r"[\x00-\x1f\x7f]", "", prompt)
    prompt = re.sub(r"\s+", " ", prompt)
    return prompt


def with_identity_guardrail(prompt: str) -> str:
    """Append the identity-preservation guardrail exactly once."""
    text = _sanitize_prompt(prompt)
    marker = "preserve the subject's exact facial identity"
    if marker.lower() in text.lower():
        return text
    return f"{text} {IDENTITY_PRESERVATION_INSTRUCTIONS}".strip()


def _parse_size(size, default=(1024, 1024)) -> tuple[int, int]:
    width, height = default
    try:
        if isinstance(size, dict):
            width = int(size.get("width") or default[0])
            height = int(size.get("height") or default[1])
        elif isinstance(size, (list, tuple)) and len(size) >= 2:
            width, height = int(size[0]), int(size[1])
        elif isinstance(size, str) and re.match(r"^\d+\s*[xX×]\s*\d+$", size.strip()):
            width, height = (int(part) for part in re.split(r"\s*[xX×]\s*", size.strip()))
    except (TypeError, ValueError):
        width, height = default
    width = max(256, min(1536, width or default[0]))
    height = max(256, min(1536, height or default[1]))
    return width, height


def _reference_parts(reference_image):
    """Return ``(bytes, url, mime)`` for a data/base64/URL reference image."""
    if isinstance(reference_image, dict):
        raw = str(reference_image.get("data") or "").strip()
        mime = str(reference_image.get("mimeType") or reference_image.get("mime_type") or "").strip()
        url = str(reference_image.get("url") or "").strip()
    elif isinstance(reference_image, str):
        raw = reference_image.strip()
        mime, url = "", ""
    else:
        return None, "", ""

    if not raw and url.startswith(("http://", "https://")):
        return None, url, mime

    if raw.startswith(("http://", "https://")):
        return None, raw, mime

    if raw.startswith("data:"):
        header, _, payload = raw.partition(",")
        if not mime and ";" in header:
            mime = header[5:].split(";", 1)[0].strip()
        raw = payload.strip()

    if not raw:
        return None, url, mime
    try:
        image_bytes = base64.b64decode(raw, validate=False)
    except Exception:
        image_bytes = b""
    if not image_bytes:
        raise RuntimeError("The attached reference image could not be read.")
    if len(image_bytes) > MAX_REFERENCE_BYTES:
        raise RuntimeError("The attached reference image is too large.")
    return image_bytes, url, mime or "image/jpeg"


def _download_reference(url: str) -> tuple[bytes, str]:
    response = requests.get(url, timeout=30)
    if response.status_code != 200 or not response.content:
        raise RuntimeError(f"Reference image download failed: HTTP {response.status_code}")
    if len(response.content) > MAX_REFERENCE_BYTES:
        raise RuntimeError("The attached reference image is too large.")
    mime = response.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    return response.content, mime or "image/jpeg"


def _host_reference_image(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Give the key-free image-to-image path a short-lived public URL."""
    ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(mime, "jpg")
    filename = f"vm-reference-{uuid.uuid4().hex}.{ext}"
    attempts = (
        (TEMP_IMAGE_HOST, "file", {"expires": "24"}),
        (TEMP_IMAGE_FALLBACK_HOST, "fileToUpload", {"reqtype": "fileupload", "time": "1h"}),
    )
    for endpoint, field, form in attempts:
        try:
            response = requests.post(
                endpoint,
                data=form,
                files={field: (filename, image_bytes, mime)},
                timeout=30,
            )
            hosted = response.text.strip()
            if response.status_code in (200, 201) and re.match(r"^https?://\S+$", hosted):
                print("[IMAGE FLOW] Reference image hosted for editing")
                return hosted
        except Exception as exc:
            print(f"[IMAGE FLOW] Reference hosting attempt skipped: {exc}")
    raise RuntimeError("Could not prepare the reference image for editing.")


def _content_extension(content_type: str, data: bytes) -> str:
    mime = str(content_type or "").split(";")[0].strip().lower()
    if mime.startswith("image/"):
        subtype = mime.split("/", 1)[1]
        return "jpg" if subtype == "jpeg" else subtype
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    return "png"


def _call_hf_image(
    prompt: str,
    image_bytes: bytes | None = None,
    size=(1024, 1024),
    model_id: str | None = None,
    timeout: int = 120,
) -> tuple[bytes, str]:
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Cloud editing token unavailable")
    model = model_id or (HF_KONTEXT_MODEL if image_bytes else HF_SCHNELL_MODEL)
    url = f"{HF_INFERENCE_BASE}/{quote(model, safe='/')}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if image_bytes is not None:
        payload = {
            "inputs": base64.b64encode(image_bytes).decode("ascii"),
            "parameters": {
                "prompt": prompt,
                "target_size": {"width": size[0], "height": size[1]},
            },
        }
    else:
        payload = {
            "inputs": prompt,
            "parameters": {"width": size[0], "height": size[1]},
        }
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code != 200 or not response.content:
        detail = str(getattr(response, "text", ""))[:300]
        raise RuntimeError(f"Cloud image HTTP {response.status_code}: {detail}")
    return response.content, response.headers.get("content-type", "image/png")


def _call_pollinations(
    prompt: str,
    reference_url: str | None = None,
    size=(1024, 1024),
    model_id: str | None = None,
    timeout: int = 90,
) -> tuple[bytes, str]:
    model = model_id or ("kontext" if reference_url else "flux")
    params = {
        "model": model,
        "width": size[0],
        "height": size[1],
        "nologo": "true",
    }
    if reference_url:
        params["image"] = reference_url
    url = f"{POLLINATIONS_BASE}/{quote(prompt, safe='')}"
    response = requests.get(url, params=params, timeout=timeout)
    if response.status_code != 200 or not response.content:
        detail = str(getattr(response, "text", ""))[:300]
        raise RuntimeError(f"Image HTTP {response.status_code}: {detail}")
    return response.content, response.headers.get("content-type", "image/png")


def generate_image(
    prompt: str,
    api_key: str | None = None,
    model: str | None = None,
    enhance: bool = True,
    reference_image: dict | None = None,
    size=None,
    mode: str | None = None,
    prefer: str | None = None,
) -> dict:
    """Generate (or edit) an image and save it to local static storage.

    ``prefer`` is internal routing only: ``"hf"`` forces the primary path,
    ``"pollinations"`` forces the zero-key path, and the default tries the
    primary path first then falls back automatically on failure/rate limit.
    """
    print("[IMAGE] Request Started")
    reference_bytes, reference_url, mime = _reference_parts(reference_image)
    if reference_bytes is None and reference_url:
        reference_bytes, mime = _download_reference(reference_url)

    is_edit = bool(reference_bytes) or str(mode or "").strip().lower() in (
        "i2i", "image_to_image", "image-to-image", "edit", "edit_image"
    )
    if is_edit and not reference_bytes:
        raise RuntimeError("A reference image is required for editing requests.")

    resolved_prompt = _enhance_prompt(prompt, api_key) if enhance else prompt
    resolved_prompt = _sanitize_prompt(resolved_prompt)
    if not resolved_prompt:
        raise RuntimeError("Prompt is empty after sanitization.")

    # Guardrail is applied AFTER enhancement so it can never be rewritten away.
    # The user-facing revised_prompt stays clean; effective_prompt is internal.
    effective_prompt = with_identity_guardrail(resolved_prompt) if is_edit else resolved_prompt
    width, height = _parse_size(size)

    requested_model = str(model or "").strip() or None
    # Internal convenience prefix: force primary routing without leaking it
    # into the provider's model path.
    forced_hf = bool(requested_model and requested_model.lower().startswith("hf:"))
    if forced_hf:
        requested_model = requested_model[3:].strip() or None
    lowered_model = (requested_model or "").lower()
    if prefer in (None, "", "auto"):
        engines = ["hf", "pollinations"]
    elif str(prefer).lower() in ("hf", "huggingface", "hugging face"):
        engines = ["hf"]
    else:
        engines = ["pollinations"]
    if forced_hf:
        engines = ["hf"]
        if lowered_model == "kontext":
            requested_model = HF_KONTEXT_MODEL
        elif lowered_model == "flux":
            requested_model = HF_SCHNELL_MODEL
    elif "kontext-dev" in lowered_model or "schnell" in lowered_model:
        engines = ["hf"]
    elif lowered_model in ("kontext", "flux"):
        engines = ["pollinations"]

    last_error: Exception | None = None
    for engine in engines:
        try:
            if engine == "hf":
                data, content_type = _call_hf_image(
                    effective_prompt,
                    reference_bytes if is_edit else None,
                    (width, height),
                    model_id=requested_model or None,
                )
            else:
                hosted_url = ""
                if is_edit:
                    hosted_url = reference_url or _host_reference_image(reference_bytes, mime)
                data, content_type = _call_pollinations(
                    effective_prompt,
                    hosted_url or None,
                    (width, height),
                    model_id=("kontext" if is_edit else "flux") if not requested_model else requested_model,
                )
            image_url = _save_image(data, _content_extension(content_type, data))
            print(f"[IMAGE] Request Success — mode={'edit' if is_edit else 'text'}")
            return {
                "image_url": image_url,
                "revised_prompt": resolved_prompt,
                "effective_prompt": effective_prompt,
                "text": "",
                "mode": "image_to_image" if is_edit else "text_to_image",
            }
        except Exception as exc:
            last_error = exc
            print(f"[IMAGE FLOW] {engine} attempt failed: {exc}")

    raise RuntimeError(str(last_error) if last_error else "Image generation failed.")
