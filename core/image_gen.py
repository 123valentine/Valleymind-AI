"""Resilient image generation and identity-preserving reference editing.

Automatic chain:
    1. Google AI Studio ``gemini-2.5-flash-image`` when a Google key is present
    2. Hugging Face (FLUX.1 Kontext for edits, FLUX.1 schnell for text)
       when ``HF_TOKEN`` is available
    3. Pollinations ``kontext`` (edits) or ``flux`` (text) as the permanent
       backup

An edit never falls back to a text-only model: without an authenticated
identity-preserving path the reference photo is preserved and a clean retry
message is returned. Provider names never appear in user-facing copy; all
provider detail is internal and only logged server-side.
"""

import base64
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

from core import hf_connectors
from core.config import PROJECT_ROOT
from core.failover import GENERIC_PROVIDER_ERROR, is_retryable_provider_error


# A reference photo is never replaced with an unrelated face. When every
# identity-preserving path is busy, the UI keeps the attachment and asks for a
# retry without naming any provider.
REFERENCE_EDIT_RETRY_MESSAGE = (
    "Your photo is still attached, but editing is busy right now. "
    "Please try again in a moment."
)


GENERATED_DIR = PROJECT_ROOT / "static" / "generated"
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
POLLINATIONS_GEN_BASE = "https://gen.pollinations.ai/image"
GEMINI_IMAGE_BASE = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
HF_KONTEXT_MODEL = "black-forest-labs/FLUX.1-Kontext-dev"
HF_SCHNELL_MODEL = "black-forest-labs/FLUX.1-schnell"
# Free ZeroGPU editing Spaces tried when Inference Providers exhausts credits.
HF_ZEROGPU_IMAGE_SPACES = (
    os.getenv("HF_IMAGE_ZEROGPU_SPACE", "prithivMLmods/FLUX.1-Kontext-Photo-Mate-v2"),
    "black-forest-labs/FLUX.1-Kontext-Dev",
)

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


_PROVIDER_NAME_RE = re.compile(
    r"\b(?:google(?:\s+ai\s+studio)?|gemini|hugging\s*face|huggingface|"
    r"pollinations|flux(?:\.1)?(?:\s+kontext|\s+schnell)?|qwen|alibaba)\b",
    re.IGNORECASE,
)


def _scrub_provider_names(text: str) -> str:
    """Keep infrastructure names out of user-visible prompt/card copy."""
    return _PROVIDER_NAME_RE.sub("AI", _sanitize_prompt(text))


def _google_image_api_key(api_key: str | None = None) -> str:
    """Accept the app config key or any common Google key variable name."""
    candidates = (
        api_key,
        os.getenv("GEMINI_API_KEY"),
        os.getenv("GOOGLE_API_KEY"),
        os.getenv("GOOGLE_GENERATIVE_AI_API_KEY"),
    )
    for candidate in candidates:
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _pollinations_api_key() -> str:
    return (os.getenv("POLLINATIONS_API_KEY") or "").strip()


def _pollinations_image_base() -> str:
    """Use the authenticated gen endpoint when a key exists, else the free host."""
    return POLLINATIONS_GEN_BASE if _pollinations_api_key() else POLLINATIONS_BASE


def _gemini_aspect_ratio(width: int, height: int) -> str:
    ratio = width / max(1, height)
    return min(
        ((1, "1:1"), (3 / 4, "3:4"), (4 / 3, "4:3"), (9 / 16, "9:16"), (16 / 9, "16:9")),
        key=lambda item: abs(ratio - item[0]),
    )[1]


def _extract_gemini_image(payload) -> tuple[bytes, str]:
    """Find inline image data in generateContent or interactions responses."""
    def walk(node):
        if isinstance(node, dict):
            inline = node.get("inlineData") or node.get("inline_data") or {}
            if isinstance(inline, dict):
                encoded = inline.get("data") or inline.get("b64_json")
                if encoded:
                    mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                    return base64.b64decode(encoded), mime
            for key in ("outputImage", "output_image"):
                candidate = node.get(key)
                if isinstance(candidate, dict):
                    encoded = candidate.get("data") or candidate.get("b64_json")
                    if encoded:
                        mime = candidate.get("mimeType") or candidate.get("mime_type") or "image/png"
                        return base64.b64decode(encoded), mime
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = walk(value)
                if found:
                    return found
        return None

    found = walk(payload)
    if not found:
        raise RuntimeError("Image response contained no image data.")
    return found


def _call_gemini_image(
    prompt: str,
    api_key: str | None = None,
    reference_bytes: bytes | None = None,
    mime: str = "image/jpeg",
    size=(1024, 1024),
    model_id: str | None = None,
    timeout: int = 120,
) -> tuple[bytes, str]:
    key = _google_image_api_key(api_key)
    if not key:
        raise RuntimeError("Image provider key unavailable")

    parts = [{"text": prompt}]
    if reference_bytes is not None:
        parts.append(
            {
                "inline_data": {
                    "mime_type": mime or "image/jpeg",
                    "data": base64.b64encode(reference_bytes).decode("ascii"),
                }
            }
        )

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": _gemini_aspect_ratio(size[0], size[1])},
        },
    }
    model = model_id or GEMINI_IMAGE_MODEL
    url = f"{GEMINI_IMAGE_BASE}/models/{quote(model, safe='/')}:generateContent"
    response = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        detail = str(getattr(response, "text", ""))[:300]
        raise RuntimeError(f"Image HTTP {response.status_code}: {detail}")
    return _extract_gemini_image(response.json())


def _call_inference_image(
    prompt: str,
    image_bytes: bytes | None = None,
    size=(1024, 1024),
    model_id: str | None = None,
) -> bytes:
    """Call the active Hugging Face Router through InferenceClient."""
    client = hf_connectors.inference_client()
    if image_bytes is not None:
        image = client.image_to_image(
            image_bytes,
            prompt=prompt,
            model=model_id or HF_KONTEXT_MODEL,
        )
    else:
        image = client.text_to_image(
            prompt=prompt,
            model=model_id or HF_SCHNELL_MODEL,
            width=int(size[0]),
            height=int(size[1]),
        )
    data = hf_connectors.pil_to_bytes(image)
    if not data:
        raise RuntimeError("Inference response contained no image")
    return data


def _call_zerogpu_image(
    prompt: str,
    image_bytes: bytes,
    size=(1024, 1024),
) -> bytes:
    """Free ZeroGPU editing fallback when Router credits/permissions fail."""
    last_error: Exception | None = None
    for space in HF_ZEROGPU_IMAGE_SPACES:
        try:
            data = hf_connectors.space_media(
                space,
                prompt=prompt,
                image_bytes=image_bytes,
                size=(int(size[0]), int(size[1])),
                timeout=int(os.getenv("HF_IMAGE_ZEROGPU_TIMEOUT", "240")),
            )
            if data:
                print("[IMAGE FLOW] ZeroGPU edit completed")
                return data
        except Exception as exc:
            last_error = exc
            print(f"[IMAGE FLOW] ZeroGPU edit attempt failed: {exc}")
    raise last_error or RuntimeError("ZeroGPU editing unavailable")


def _call_hf_image(
    prompt: str,
    image_bytes: bytes | None = None,
    size=(1024, 1024),
    model_id: str | None = None,
    timeout: int = 120,
) -> tuple[bytes, str]:
    """Router inference first; free ZeroGPU edits, then Pollinations upstream."""
    if not hf_connectors.hf_token():
        raise RuntimeError("Cloud editing token unavailable")
    try:
        return _call_inference_image(prompt, image_bytes, size, model_id), "image/png"
    except Exception as exc:
        # Text-to-image hands straight off to the free backup (outer chain).
        # Reference edits get the free ZeroGPU tier for any retryable outage,
        # not only credit failures, before the authenticated edit backup.
        if image_bytes is None or not is_retryable_provider_error(exc):
            raise
        print(f"[IMAGE FLOW] Router edit unavailable, trying ZeroGPU: {exc}")
    return _call_zerogpu_image(prompt, image_bytes, size), "image/png"


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
    key = _pollinations_api_key()
    if key:
        params["key"] = key
    url = f"{_pollinations_image_base()}/{quote(prompt, safe='')}"
    headers = {"Authorization": f"Bearer {key}"} if key else None
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    if response.status_code != 200 or not response.content:
        detail = str(getattr(response, "text", ""))[:300]
        raise RuntimeError(f"Image HTTP {response.status_code}: {detail}")
    return response.content, response.headers.get("content-type", "image/png")


def _is_text_only_model(value: str | None) -> bool:
    """True for flux/schnell model ids that ignore a reference image."""
    lowered = str(value or "").lower()
    return bool(re.search(r"flux|schnell", lowered)) and "kontext" not in lowered


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

    ``prefer`` is internal routing only. The default tries the full automatic
    chain (Google → Hugging Face → Pollinations); a provider name starts the
    chain at that provider while keeping later free/backup paths available.
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
    if is_edit and _is_text_only_model(model):
        raise RuntimeError(REFERENCE_EDIT_RETRY_MESSAGE)

    resolved_prompt = _enhance_prompt(prompt, api_key) if enhance else prompt
    resolved_prompt = _sanitize_prompt(resolved_prompt)
    if not resolved_prompt:
        raise RuntimeError("Prompt is empty after sanitization.")

    # Guardrail is applied AFTER enhancement so it can never be rewritten away.
    # The user-facing revised_prompt stays clean; effective_prompt is internal.
    effective_prompt = with_identity_guardrail(resolved_prompt) if is_edit else resolved_prompt
    width, height = _parse_size(size)

    requested_model = str(model or "").strip() or None
    lowered_model = (requested_model or "").lower()
    start_engine = None

    # Internal convenience prefixes force a starting provider without leaking
    # infrastructure names into the provider's model path.
    for prefix, engine in (("gemini:", "gemini"), ("google:", "gemini"), ("hf:", "hf")):
        if lowered_model.startswith(prefix):
            start_engine = engine
            requested_model = requested_model[len(prefix):].strip() or None
            lowered_model = (requested_model or "").lower()
            break

    preference = str(prefer or "").strip().lower()
    forced_only = None
    if preference in ("gemini_only", "gemini-only"):
        forced_only = start_engine = "gemini"
    elif preference in ("hf_only", "hf-only"):
        forced_only = start_engine = "hf"
    elif preference in ("pollinations", "pollinations_only", "pollinations-only", "free", "backup"):
        forced_only = start_engine = "pollinations"

    if start_engine is None:
        if preference in ("gemini", "google", "google ai studio"):
            start_engine = "gemini"
        elif preference in ("hf", "huggingface", "hugging face"):
            start_engine = "hf"
        elif "gemini" in lowered_model or "google" in lowered_model:
            start_engine = "gemini"
        elif "kontext-dev" in lowered_model or "schnell" in lowered_model:
            start_engine = "hf"
        elif lowered_model in ("kontext", "flux"):
            start_engine = "pollinations"

    if start_engine == "hf" and lowered_model in ("kontext", "flux"):
        requested_model = HF_KONTEXT_MODEL if lowered_model == "kontext" else HF_SCHNELL_MODEL

    chain = ["gemini", "hf", "pollinations"]
    if forced_only:
        engines = [forced_only]
    elif start_engine == "pollinations":
        engines = ["pollinations"]
    elif start_engine:
        engines = chain[chain.index(start_engine):]
    else:
        engines = list(chain)

    # A model override belongs to the provider that was explicitly selected;
    # backup providers must receive their own defaults, never that model id.
    engine_models = {
        "gemini": requested_model if start_engine == "gemini" else None,
        "hf": requested_model if start_engine == "hf" else None,
        "pollinations": requested_model if start_engine == "pollinations" else None,
    }

    last_error: Exception | None = None
    for engine in engines:
        try:
            if engine == "gemini":
                data, content_type = _call_gemini_image(
                    effective_prompt,
                    api_key,
                    reference_bytes if is_edit else None,
                    mime,
                    (width, height),
                    model_id=engine_models["gemini"],
                )
            elif engine == "hf":
                data, content_type = _call_hf_image(
                    effective_prompt,
                    reference_bytes if is_edit else None,
                    (width, height),
                    model_id=engine_models["hf"],
                )
            else:
                hosted_url = ""
                own_model = engine_models["pollinations"]
                if is_edit:
                    # The text-only host ignores the reference image and would
                    # return an unrelated person. Never call it for an edit.
                    if not _pollinations_api_key():
                        raise RuntimeError(REFERENCE_EDIT_RETRY_MESSAGE)
                    if _is_text_only_model(own_model):
                        raise RuntimeError(REFERENCE_EDIT_RETRY_MESSAGE)
                    hosted_url = reference_url or _host_reference_image(reference_bytes, mime)
                    pollinations_model = "kontext"
                else:
                    pollinations_model = "flux"
                    if own_model:
                        if "kontext" in own_model.lower():
                            pollinations_model = "kontext"
                        elif "schnell" in own_model.lower() or own_model.lower() == "flux":
                            pollinations_model = "flux"
                        else:
                            pollinations_model = own_model
                data, content_type = _call_pollinations(
                    effective_prompt,
                    hosted_url or None,
                    (width, height),
                    model_id=pollinations_model,
                )
            image_url = _save_image(data, _content_extension(content_type, data))
            print(f"[IMAGE] Request Success — mode={'edit' if is_edit else 'text'} — path={engine}")
            return {
                "image_url": image_url,
                "revised_prompt": _scrub_provider_names(resolved_prompt),
                "effective_prompt": effective_prompt,
                "text": "",
                "mode": "image_to_image" if is_edit else "text_to_image",
            }
        except Exception as exc:
            last_error = exc
            # Internal classification only: every provider failure hands off so
            # a retired model on one provider cannot strand the request.
            retryable = is_retryable_provider_error(exc)
            print(f"[IMAGE FLOW] {engine} attempt failed (retryable={retryable}): {exc}")

    if is_edit:
        raise RuntimeError(REFERENCE_EDIT_RETRY_MESSAGE)
    raise RuntimeError(GENERIC_PROVIDER_ERROR)
