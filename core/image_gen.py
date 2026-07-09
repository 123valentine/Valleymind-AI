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


def _call_gemini_text(prompt: str, system_prompt: str, api_key: str, model: str = "gemini-2.0-flash", timeout: int = 15) -> str:
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
        raise RuntimeError(f"Gemini text HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini text returned empty candidates.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
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
    prompt = prompt.strip()
    prompt = re.sub(r"[\x00-\x1f\x7f]", "", prompt)
    prompt = re.sub(r"\s+", " ", prompt)
    return prompt


def generate_image(
    prompt: str,
    api_key: str | None = None,
    model: str | None = None,
    enhance: bool = True,
    reference_image: dict | None = None,
) -> dict:
    print("[IMAGE] Request Started")
    resolved_prompt = _enhance_prompt(prompt, api_key) if enhance else prompt
    resolved_prompt = _sanitize_prompt(resolved_prompt)

    if not resolved_prompt:
        raise RuntimeError("Prompt is empty after sanitization.")

    encoded = quote(resolved_prompt)
    url = f"{POLLINATIONS_BASE}/{encoded}"

    print(f"[IMAGE FLOW] Pollinations request started — URL: {url[:150]}")
    print(f"[IMAGE FLOW] Pollinations request started — prompt: {resolved_prompt[:200]}")

    if reference_image:
        print("[IMAGE GEN] Note: reference_image is not supported with Pollinations Flux (text-to-image only). Ignoring.")

    try:
        print(f"[IMAGE FLOW] Pollinations request started")
        response = requests.get(url, timeout=60)
        print(f"[IMAGE FLOW] Pollinations response received — status: {response.status_code}, type: {response.headers.get('content-type', 'unknown')}, size: {len(response.content)} bytes")
        if response.status_code != 200:
            raise RuntimeError(
                f"Pollinations HTTP {response.status_code}: {response.text[:500]}"
            )
    except requests.exceptions.Timeout:
        raise RuntimeError("Image generation timed out. Please try again.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Could not connect to image generation service. Check your internet connection.")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Image generation failed: {e}")

    content_type = response.headers.get("content-type", "image/png")
    ext = content_type.split("/")[-1] if "/" in content_type else "png"

    image_url = _save_image(response.content, ext)

    print(f"[IMAGE FLOW] Returning image_url: {image_url}")
    print("[IMAGE] Request Success")
    return {
        "image_url": image_url,
        "revised_prompt": resolved_prompt,
        "text": "",
    }
