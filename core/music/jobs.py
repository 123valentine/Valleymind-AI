"""Async job state machine for heavy Music workstation work.

Music generation, stem separation, vocal processing and mastering can take a
long time and must never block the request thread or the rest of ValleyMind.
Every heavy operation is submitted here and returns a job id immediately; the
client polls ``GET /api/music/jobs/<id>``.

States (exactly as the workstation surfaces them)::

    QUEUED → GENERATING → PROCESSING → COMPLETED
                              │
                              ├─→ FAILED
                              └─→ CANCELLED

Honesty rules:

  * ``progress`` is ``None`` whenever the underlying step cannot report real
    progress. The UI then shows an indeterminate state — this engine never
    fabricates a percentage.
  * a provider that cannot perform an operation surfaces a ``FAILED`` job with
    ``error_code='provider_unavailable'`` and the provider's own explanation,
    never a fake success.

Jobs live in memory for the life of the process and are mirrored to Mongo
(``music_jobs``) when available so they survive a browser reload / worker
restart. With no Mongo the in-memory registry is the source of truth.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from core.db import get_db
from core.music.providers import MusicError, ProviderUnavailable

# Job states.
QUEUED = "QUEUED"
GENERATING = "GENERATING"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

TERMINAL = (COMPLETED, FAILED, CANCELLED)

_JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()
_CANCEL: dict[str, threading.Event] = {}
_MAX_KEPT = 200

try:
    import os as _os
    _WORKERS = max(1, int(_os.getenv("MUSIC_JOB_WORKERS", "2")))
except (TypeError, ValueError):
    _WORKERS = 2
_POOL = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="music-job")


def _now() -> int:
    return int(time.time())


def _mongo():
    try:
        db = get_db()
    except Exception:  # noqa: BLE001
        return None
    return getattr(db, "music_jobs", None) if db is not None else None


def _persist(job: dict) -> None:
    coll = _mongo()
    if coll is None:
        return
    try:
        doc = dict(job)
        doc["_id"] = doc["id"]
        coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    except Exception:  # noqa: BLE001 — persistence is best-effort
        pass


def _load_from_mongo(job_id: str) -> dict | None:
    coll = _mongo()
    if coll is None:
        return None
    try:
        doc = coll.find_one({"_id": job_id})
        if doc:
            doc.pop("_id", None)
            return doc
    except Exception:  # noqa: BLE001
        pass
    return None


def _trim() -> None:
    if len(_JOBS) <= _MAX_KEPT:
        return
    done = sorted(
        (j for j in _JOBS.values() if j["status"] in TERMINAL),
        key=lambda j: j.get("updated_at", 0),
    )
    for job in done[: len(_JOBS) - _MAX_KEPT]:
        _JOBS.pop(job["id"], None)
        _CANCEL.pop(job["id"], None)


def submit(user_id: str, kind: str, fn, *, meta: dict | None = None) -> dict:
    """Queue ``fn(progress, is_cancelled)`` and return the public job object.

    ``fn`` must return a JSON-serialisable ``dict`` (the job result). It may
    call ``progress(fraction_or_None)`` any number of times.
    """
    job_id = "mj_" + uuid.uuid4().hex[:20]
    job = {
        "id": job_id,
        "user_id": str(user_id or ""),
        "kind": str(kind or "music"),
        "status": QUEUED,
        "progress": None,
        "result": {},
        "error": "",
        "error_code": "",
        "meta": meta or {},
        "created_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
    }
    event = threading.Event()
    with _LOCK:
        _JOBS[job_id] = job
        _CANCEL[job_id] = event
    _persist(job)

    def _run():
        _update(job_id, status=GENERATING, progress=None)

        def progress(value):
            _update(job_id, progress=value)

        def is_cancelled() -> bool:
            return event.is_set()

        try:
            if is_cancelled():
                _update(job_id, status=CANCELLED)
                return
            result = fn(progress, is_cancelled)
            if is_cancelled():
                _update(job_id, status=CANCELLED)
                return
            _update(job_id, status=COMPLETED, progress=1.0,
                    result=result if isinstance(result, dict) else {"value": result})
        except ProviderUnavailable as exc:
            _update(job_id, status=FAILED, progress=None, error=str(exc),
                    error_code="provider_unavailable")
        except MusicError as exc:
            _update(job_id, status=FAILED, progress=None, error=str(exc),
                    error_code=getattr(exc, "code", "music_error"))
        except Exception as exc:  # noqa: BLE001
            _update(job_id, status=FAILED, progress=None,
                    error=f"Music job failed: {exc}", error_code="job_error")
            try:
                traceback.print_exc()
            except Exception:  # noqa: BLE001
                pass
        finally:
            _update(job_id, updated_at=_now())

    _POOL.submit(_run)
    return public(job)


def _update(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = _now()
        if job["status"] in TERMINAL and not job.get("finished_at"):
            job["finished_at"] = _now()
        snapshot = dict(job)
    _persist(snapshot)


def get(user_id: str, job_id: str) -> dict | None:
    """Return a job scoped to its owner (or ``None``)."""
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        job = _load_from_mongo(job_id)
        if job:
            with _LOCK:
                _JOBS.setdefault(job_id, job)
    if not job:
        return None
    if str(job.get("user_id")) != str(user_id):
        return None
    return public(job)


def list_for_user(user_id: str, limit: int = 50) -> list:
    with _LOCK:
        jobs = [j for j in _JOBS.values() if str(j.get("user_id")) == str(user_id)]
    jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return [public(j) for j in jobs[: max(1, int(limit))]]


def cancel(user_id: str, job_id: str) -> bool:
    job = get(user_id, job_id)
    if not job or job["status"] in TERMINAL:
        return False
    event = _CANCEL.get(job_id)
    if event is not None:
        event.set()
    _update(job_id, status=CANCELLED)
    return True


def public(job: dict) -> dict:
    """Sanitised, client-facing view of a job (drops internal user id)."""
    return {
        "id": job.get("id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "result": job.get("result") or {},
        "error": job.get("error") or "",
        "error_code": job.get("error_code") or "",
        "meta": job.get("meta") or {},
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
    }


def wait_for(user_id: str, job_id: str, timeout: float = 30.0, interval: float = 0.02) -> dict | None:
    """Block until a job finishes (used by the test-suite and sync fallbacks)."""
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        job = get(user_id, job_id)
        if job and job["status"] in TERMINAL:
            return job
        time.sleep(interval)
    return get(user_id, job_id)
