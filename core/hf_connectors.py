"""Shared Hugging Face Router and ZeroGPU Space connectors.

The Router calls go through ``huggingface_hub.InferenceClient`` (which uses
``router.huggingface.co`` internally); Space calls go through
``gradio_client.Client``. Imports are lazy so an unavailable optional client
only disables this provider — the outer failover chain keeps working.

Upstream detail is internal only. Callers translate exhausted chains into
provider-neutral user copy.
"""

from __future__ import annotations

import base64
import io
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

import requests

# Credit/quota/permission failures are the normal reason to try a free Space.
CREDIT_STATUS_CODES = {400, 401, 402, 403, 408, 409, 410, 425, 429}
_CREDIT_TEXT_RE = re.compile(
    r"(credit|quota|billing|payment|insufficient|unauthori[sz]ed|forbidden|"
    r"rate.?limit|too many requests|exceeded|no payment method)",
    re.IGNORECASE,
)
_HANDOFF_TEXT_RE = re.compile(
    r"(timeout|timed? ?out|queue|overloaded|busy|deprecated|no longer supported|"
    r"not supported|gone|server error|service unavailable|bad gateway|temporarily "
    r"unavailable|generation failed)",
    re.IGNORECASE,
)


def hf_token() -> str:
    return (os.getenv("HF_TOKEN") or "").strip()


def _status_code(error: Any) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    if response is not None:
        for attr in ("status_code", "code", "http_status"):
            value = getattr(response, attr, None)
            if isinstance(value, int):
                return value
    if isinstance(error, dict):
        for key in ("status", "status_code", "code"):
            value = error.get(key)
            if isinstance(value, int):
                return value
    return None


def is_credit_error(error: Any) -> bool:
    """True for quota/credit/permission failures that justify a free Space."""
    status = _status_code(error)
    if status is not None and status in CREDIT_STATUS_CODES:
        return True
    return bool(_CREDIT_TEXT_RE.search(str(error)))


def is_handoff_error(error: Any) -> bool:
    """True when this failure should advance to the next availability tier."""
    if is_credit_error(error):
        return True
    status = _status_code(error)
    if status is not None and 500 <= status <= 599:
        return True
    if _HANDOFF_TEXT_RE.search(str(error)):
        return True
    return any(word in type(error).__name__.lower() for word in ("timeout", "connecterror"))


def inference_client(token: str | None = None):
    """``InferenceClient`` routes through router.huggingface.co automatically."""
    from huggingface_hub import InferenceClient

    return InferenceClient(token=token or hf_token())


def space_client(space: str, token: str | None = None, verbose: bool = False):
    from gradio_client import Client

    return Client(space, token=token or hf_token(), verbose=verbose)


def pil_to_bytes(image: Any) -> bytes:
    """Normalise an InferenceClient image result into PNG bytes."""
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _normalise(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def _is_image_param(parameter: Any) -> bool:
    if not isinstance(parameter, dict):
        return False
    key = _normalise(str(parameter.get("label") or parameter.get("name") or ""))
    return any(word in key for word in ("image", "photo", "upload", "reference"))


def _pick_endpoint(api: dict[str, Any], prefer_image: bool = False) -> tuple[str | None, dict[str, Any]]:
    """Choose a generation endpoint from the Space's current Gradio schema."""
    endpoints = (api or {}).get("named_endpoints") or {}
    names = list(endpoints)
    if not names:
        return None, {}
    generation = [
        name for name in names
        if any(word in name.lower() for word in ("run", "generate", "infer", "predict"))
    ]
    if prefer_image:
        for name in generation + names:
            if any(_is_image_param(p) for p in (endpoints[name].get("parameters") or [])):
                return name, endpoints[name]
    for name in generation or names:
        return name, endpoints[name]
    return None, {}


def _param_key(parameter: dict[str, Any], label: str) -> str:
    """Gradio.predict wants the schema's parameter_name, not its display label."""
    return str(parameter.get("parameter_name") or parameter.get("name") or label)


def _image_input(path: str | None):
    """Gradio 5+ expects a FileData payload rather than a bare file path."""
    if not path:
        return None
    try:
        from gradio_client import handle_file
    except ImportError:
        return path
    return handle_file(path)


def _bounded_number(parameter: dict[str, Any], value: int) -> int:
    """Honor numeric ranges Gradio publishes in the parameter schema."""
    schema_type = parameter.get("type")
    description = schema_type.get("description", "") if isinstance(schema_type, dict) else ""
    match = re.search(
        r"between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)",
        str(description),
        re.IGNORECASE,
    )
    if match:
        low, high = (float(match.group(1)), float(match.group(2)))
        value = int(min(high, max(low, value)))
    return int(value)


def _predict_kwargs(
    endpoint: dict[str, Any],
    prompt: str,
    image_path: str | None,
    size: tuple[int, int],
) -> dict[str, Any] | None:
    """Map our inputs onto the parameter names the Space exposes today."""
    kwargs: dict[str, Any] = {}
    for parameter in endpoint.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        label = str(parameter.get("label") or parameter.get("name") or "").strip()
        if not label:
            continue
        name = _param_key(parameter, label)
        key = _normalise(label)
        if "image" in key or "photo" in key or "upload" in key or "reference" in key:
            kwargs[name] = _image_input(image_path)
        elif key in ("prompt", "edit prompt", "text prompt", "instruction", "caption") or (
            key.startswith("prompt") and "enhance" not in key and "negative" not in key
        ):
            kwargs[name] = prompt
        elif key.startswith("height"):
            kwargs[name] = _bounded_number(parameter, size[1])
        elif key.startswith("width"):
            kwargs[name] = _bounded_number(parameter, size[0])
        elif key.startswith("duration"):
            kwargs[name] = _bounded_number(parameter, 4)
        elif key == "seed":
            kwargs[name] = _bounded_number(parameter, 0)
        elif parameter.get("required") is True:
            return None
        # Optional knobs (LoRA, decoder, guidance, booleans) keep their defaults.
    return kwargs


def _media_bytes(value: Any, depth: int = 0) -> bytes:
    """Find a generated file in any Gradio return shape."""
    if depth > 6:
        return b""
    if isinstance(value, dict):
        for key in ("path", "name", "video", "image", "file", "url", "data", "result"):
            if key in value:
                found = _media_bytes(value[key], depth + 1)
                if found:
                    return found
        for item in value.values():
            found = _media_bytes(item, depth + 1)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _media_bytes(item, depth + 1)
            if found:
                return found
    elif isinstance(value, str):
        if value.startswith("data:"):
            try:
                return base64.b64decode(value.partition(",")[2])
            except Exception:
                pass
        if os.path.exists(value):
            try:
                return Path(value).read_bytes()
            except OSError:
                pass
        if value.startswith(("http://", "https://")):
            try:
                response = requests.get(value, timeout=60)
                if response.status_code == 200 and response.content:
                    return response.content
            except Exception:
                pass
    elif isinstance(value, (bytes, bytearray)) and len(value) > 64:
        return bytes(value)
    return b""


def _bounded(call: Callable[[], Any], timeout: int) -> Any:
    """Bound a blocking Space call so queue timeouts hand off promptly."""
    if not timeout or timeout <= 0:
        return call()
    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        future = pool.submit(call)
        return future.result(timeout=timeout)
    except FutureTimeout as exc:
        if future is not None:
            future.cancel()
        raise TimeoutError("Space request timed out") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def space_media(
    space: str,
    prompt: str,
    image_bytes: bytes | None = None,
    size: tuple[int, int] = (1024, 1024),
    timeout: int = 240,
) -> bytes:
    """Generate media through a ZeroGPU Space using its live Gradio schema."""
    client = space_client(space)
    api = client.view_api(return_format="dict", print_info=False)
    name, endpoint = _pick_endpoint(api, prefer_image=image_bytes is not None)
    if not name:
        raise RuntimeError("Space exposed no generation endpoint")

    temp_path = ""
    if image_bytes is not None:
        handle = tempfile.NamedTemporaryFile(prefix="vm-hf-ref-", suffix=".png", delete=False)
        handle.write(image_bytes)
        handle.close()
        temp_path = handle.name
    try:
        kwargs = _predict_kwargs(endpoint, prompt, temp_path or None, size)
        if kwargs is None:
            raise RuntimeError("Space endpoint signature did not match the request")
        result = _bounded(lambda: client.predict(api_name=name, **kwargs), timeout)
        media = _media_bytes(result)
        if not media:
            raise RuntimeError("Space returned no media file")
        return media
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
