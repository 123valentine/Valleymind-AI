"""Cloudflare R2 object storage for media (images, clips, trailers).

R2 is S3-compatible, so this is boto3 pointed at the R2 endpoint with region
"auto". Media bytes live here instead of MongoDB GridFS — GridFS on a 512MB
Atlas M0 filled the cluster and, worse, streaming whole files back through Flask
(grid_out.read()) is what exhausted the Render instance's RAM.

Serving is by PRESIGNED URL: the browser fetches bytes straight from Cloudflare
and Flask only issues a tiny redirect, never touching the file. The bucket stays
private; links are short-lived.

HARD-FAIL policy: every write raises on failure. There is deliberately NO silent
fallback to ephemeral local disk — that fallback is exactly what hid the last
outage (a "saved" file that vanished on the next deploy).
"""

from __future__ import annotations

import os
import threading

# Ensure .env is loaded even when this module is used standalone (tests,
# migration scripts) and core.config hasn't been imported yet.
try:
    from core.config import PROJECT_ROOT  # importing config also runs load_dotenv
except Exception:
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_client = None
_client_lock = threading.Lock()

# Object key mirrors the app's local_path minus the "/static/media/" prefix, so
# the serve route can presign without a database lookup:
#   local_path  /static/media/users/<uid>/videos/<file>
#   r2_key                  users/<uid>/videos/<file>
_KEY_PREFIX = os.getenv("R2_KEY_PREFIX", "").strip().strip("/")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def available() -> bool:
    """True only when all five R2 settings are present."""
    return all(_env(k) for k in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET", "R2_ENDPOINT",
    ))


def bucket() -> str:
    return _env("R2_BUCKET")


def client():
    """Lazily build a boto3 S3 client aimed at R2. Cached across calls."""
    global _client
    if _client is not None:
        return _client
    if not available():
        raise RuntimeError("R2 is not configured (missing R2_* env vars)")
    with _client_lock:
        if _client is None:
            import boto3
            from botocore.config import Config
            _client = boto3.client(
                "s3",
                endpoint_url=_env("R2_ENDPOINT"),
                aws_access_key_id=_env("R2_ACCESS_KEY_ID"),
                aws_secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
                region_name="auto",
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=15,
                    read_timeout=60,
                ),
            )
    return _client


def key_for(user_id: str, subdir: str, filename: str) -> str:
    """Build the R2 object key for a media file."""
    parts = [p for p in (_KEY_PREFIX, "users", user_id, subdir, filename) if p]
    return "/".join(parts)


def key_for_subpath(user_id: str, subpath: str) -> str:
    """R2 key for a serve route's ``/static/media/users/<uid>/<subpath>`` URL,
    where subpath is e.g. ``videos/<file>``. Matches key_for()'s output."""
    parts = [p for p in (_KEY_PREFIX, "users", user_id, str(subpath).strip("/")) if p]
    return "/".join(parts)


def key_from_local_path(local_path: str) -> str:
    """Derive the R2 key from an app '/static/media/...' path."""
    p = str(local_path or "").lstrip("/")
    for pre in ("static/media/", "media/"):
        if p.startswith(pre):
            p = p[len(pre):]
            break
    return "/".join([x for x in (_KEY_PREFIX, p) if x])


def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Upload bytes to R2. Raises RuntimeError on any failure — never silent."""
    try:
        client().put_object(Bucket=bucket(), Key=key, Body=data,
                            ContentType=content_type or "application/octet-stream")
    except Exception as exc:
        raise RuntimeError(f"R2 upload failed for {key}: {exc}") from exc


def object_exists(key: str) -> bool:
    try:
        client().head_object(Bucket=bucket(), Key=key)
        return True
    except Exception:
        return False


def object_size(key: str):
    """ContentLength of an object, or None if it isn't there."""
    try:
        return int(client().head_object(Bucket=bucket(), Key=key)["ContentLength"])
    except Exception:
        return None


def download_bytes(key: str) -> bytes:
    return client().get_object(Bucket=bucket(), Key=key)["Body"].read()


def presigned_url(key: str, expires: int = 3600) -> str:
    """A short-lived GET URL. The browser streams the object directly from
    Cloudflare; Flask only hands back this string."""
    return client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket(), "Key": key}, ExpiresIn=int(expires))


def delete_object(key: str) -> None:
    client().delete_object(Bucket=bucket(), Key=key)


def health_check() -> dict:
    """Live round-trip: put -> head -> get -> delete a tiny object. Returns a
    dict describing the result (never raises)."""
    key = f"{_KEY_PREFIX + '/' if _KEY_PREFIX else ''}_healthcheck/probe.txt"
    try:
        payload = b"valleymind-r2-ok"
        upload_bytes(key, payload, "text/plain")
        size = object_size(key)
        got = download_bytes(key)
        url = presigned_url(key, expires=60)
        delete_object(key)
        return {"ok": got == payload and size == len(payload),
                "bucket": bucket(), "roundtrip_bytes": size, "presign_ok": bool(url)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
