"""Async video jobs for ValleyMind Studio.

The Studio's creative stages (script -> scenes -> storyboards) stay in the live
SSE request because they're fast. The expensive part -- turning each storyboard
into a video clip with Alibaba Wan i2v, where each clip takes minutes -- moves
here into a background job so:

  * all clips are submitted to Alibaba in parallel (batched), not one-at-a-time
    inside a single blocking request;
  * the request returns immediately with a job id;
  * per-clip state lives in Mongo (studio_jobs), so a run survives the browser
    closing and can be resumed by the poll endpoint;
  * individual clip failures never kill the run -- we assemble what succeeded
    and report which scenes are missing.

COST CONTROL is built in, not bolted on:

  * a HARD spend cap (VIDEO_BUDGET_USD) tracked cumulatively in Mongo. A run is
    refused if its estimate would exceed the remaining budget, and it aborts
    mid-run the moment the next clip would cross the cap;
  * spend is charged conservatively at SUBMIT time (never underestimates);
  * TEST MODE / fake i2v spend nothing at all.

The driver is idempotent and resumable: it only submits clips still "pending",
only polls clips that are "running", and writes a heartbeat so the poll endpoint
can relaunch it if the process that owned it went away.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timezone

import core.video_i2v as i2v
from core.db import studio_jobs_collection, studio_runs_collection, video_spend_collection

# Clip states
PENDING, RUNNING, DONE, FAILED, ABORTED = "pending", "running", "done", "failed", "aborted"
_CLIP_TERMINAL = (DONE, FAILED, ABORTED)
# Job states
JOB_TERMINAL = ("done", "failed", "budget_capped", "aborted")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# ── Configuration (all env-overridable) ────────────────────────────────────

def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def budget_usd() -> float:
    """Hard ceiling for cumulative video spend. Defaults to a small protective
    cap so an unset var can never quietly drain a coupon."""
    return _f("VIDEO_BUDGET_USD", 20.0)


def cost_per_clip() -> float:
    """Estimated $ per clip. A deliberate over-estimate protects the budget;
    tune to real DashScope pricing. Duration affects real cost."""
    return _f("VIDEO_COST_PER_CLIP_USD", 0.5)


def image_cost() -> float:
    """Estimated $ per storyboard image. The i2v path needs one paid image per
    clip (STUDIO_IMAGE_PROVIDER defaults to QwenImage); t2v needs none, which is
    the whole cost advantage of the default path."""
    return _f("STUDIO_IMAGE_COST_USD", 0.05)


def path_costs() -> dict:
    """Per-clip cost of each video path, for the Studio's cost meter."""
    v, i = cost_per_clip(), image_cost()
    return {
        "t2v_per_clip_usd": round(v, 4),
        "i2v_per_clip_usd": round(v + i, 4),
        "video_call_usd": round(v, 4),
        "storyboard_image_usd": round(i, 4),
        "i2v_premium_usd": round(i, 4),
        "i2v_premium_pct": round((i / v * 100.0), 1) if v else 0.0,
    }


def default_clips() -> int:
    """Default trailer length ~90s at 5s/clip. Short on purpose."""
    return _i("STUDIO_DEFAULT_CLIPS", 18)


def test_clips() -> int:
    return _i("STUDIO_TEST_CLIPS", 3)


def max_clips_cap() -> int:
    """Absolute ceiling on clips per run (also bounds a 5-min trailer)."""
    return _i("STUDIO_MAX_CLIPS_CAP", 60)


def submit_batch() -> int:
    """How many clips may be in flight at once (throttle to stay under
    Alibaba's concurrency limit). Backs off automatically on 429s."""
    return max(1, _i("STUDIO_SUBMIT_BATCH", 5))


def poll_interval() -> float:
    return _f("STUDIO_POLL_INTERVAL", 8.0)


def driver_stale_secs() -> float:
    """If a job's heartbeat is older than this, the poll endpoint assumes the
    driver died and relaunches it."""
    return _f("STUDIO_DRIVER_STALE_SECS", 45.0)


def clip_duration() -> int | None:
    raw = os.getenv("VIDEO_CLIP_DURATION", "").strip()
    return int(raw) if raw.isdigit() else None


def _fake() -> bool:
    return os.getenv("STUDIO_FAKE_I2V", "").strip().lower() in ("1", "true", "yes", "on")


# ── Global spend (hard budget cap) ─────────────────────────────────────────

def global_spent() -> float:
    coll = video_spend_collection()
    if coll is None:
        return 0.0
    try:
        doc = coll.find_one({"_id": "global"}) or {}
        return float(doc.get("spent_usd", 0.0))
    except Exception as exc:
        print(f"[SPEND] read failed: {exc}")
        return 0.0


def add_spend(amount: float) -> None:
    coll = video_spend_collection()
    if coll is None or amount <= 0:
        return
    try:
        coll.update_one({"_id": "global"}, {"$inc": {"spent_usd": float(amount)}}, upsert=True)
    except Exception as exc:
        print(f"[SPEND] increment failed: {exc}")


def remaining_budget() -> float:
    return round(budget_usd() - global_spent(), 4)


def estimate_cost(n_clips: int) -> float:
    return round(max(0, n_clips) * cost_per_clip(), 2)


def can_afford(n_clips: int) -> tuple[bool, float, float]:
    """(ok, estimate, remaining). Test/fake runs always afford (they spend $0)."""
    est = estimate_cost(n_clips)
    rem = remaining_budget()
    if _fake():
        return True, 0.0, rem
    return (est <= rem), est, rem


# ── Tier gating (pure — caller supplies tier + usage) ──────────────────────

def video_access(tier: str, videos_used: int, video_limit) -> tuple[bool, str]:
    """Server-side gate. Free tier is refused outright; paid tier is capped at a
    fixed number of generations per period. Never unlimited."""
    if str(tier).lower() != "paid":
        return False, "Video generation is available on paid plans only."
    if video_limit is not None and videos_used >= int(video_limit):
        return False, f"You've reached your plan's video limit ({video_limit}) for this period."
    return True, ""


# ── Job CRUD ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job(user_id: str, scenes: list, frame_sources: dict, *, target_clips: int,
            test_mode: bool = False, notes: list | None = None,
            mode: str = "t2v", sheet_text: str = "", look: str = "") -> dict:
    """Build a job over the scenes.

    ``mode`` picks the video path per run:
      * "t2v" (default) — text-to-video straight from the scene. Better prompt
        following, and no paid storyboard image is needed as the video source.
      * "i2v" — animate a reference image (user upload / reference mode). Falls
        back to t2v for any scene that has no image.
    """
    import core.studio as studio

    wanted = [s for s in scenes if s.get("number") is not None][: max(0, target_clips)]
    clips = []
    for s in wanted:
        n = s["number"]
        image_ref = frame_sources.get(n, "")
        # i2v only where an image actually exists; otherwise this scene goes t2v.
        use_i2v = (mode == "i2v") and bool(image_ref)
        clips.append({
            "number": n,
            "title": s.get("title", f"Scene {n}"),
            "mode": "i2v" if use_i2v else "t2v",
            "motion": studio.clip_prompt(s, notes=notes),
            "prompt": studio.t2v_prompt(s, sheet_text=sheet_text, look=look, notes=notes),
            "image_ref": image_ref,
            "status": PENDING,
            "task_id": "",
            "video_url": "",
            "error": "",
        })

    job = {
        "_id": uuid.uuid4().hex,
        "user_id": user_id,
        "status": "running",
        "test_mode": bool(test_mode),
        "duration": clip_duration(),
        "clips": clips,
        "final_video": "",
        "assembly_mode": "hard_cut",
        "spend_usd": 0.0,
        "cost_per_clip": 0.0 if (test_mode or _fake()) else cost_per_clip(),
        "est_cost_usd": 0.0 if (test_mode or _fake()) else estimate_cost(len(clips)),
        "missing_scenes": [],
        "observed_concurrency": 0,
        "rate_limited_at": None,
        "error": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "heartbeat": _now_iso(),
    }
    save_job(job)
    # Record the job on the user's studio_run so a mid-run reload can reconnect.
    coll = studio_runs_collection()
    if coll is not None:
        try:
            coll.update_one({"_id": user_id},
                            {"$set": {"job_id": job["_id"], "job_status": "running"}},
                            upsert=True)
        except Exception as exc:
            print(f"[JOB] could not tag studio_run with job id: {exc}")
    return job


def get_job(job_id: str) -> dict | None:
    coll = studio_jobs_collection()
    if coll is None:
        return None
    try:
        return coll.find_one({"_id": job_id})
    except Exception as exc:
        print(f"[JOB] read failed: {exc}")
        return None


def save_job(job: dict) -> None:
    coll = studio_jobs_collection()
    if coll is None:
        return
    job["updated_at"] = _now_iso()
    try:
        coll.replace_one({"_id": job["_id"]}, job, upsert=True)
    except Exception as exc:
        print(f"[JOB] save failed: {exc}")


def public_view(job: dict) -> dict:
    """Trimmed state for the frontend + cost meter."""
    if not job:
        return {}
    clips = job.get("clips", [])
    done = [c for c in clips if c.get("status") == DONE]
    return {
        "job_id": job.get("_id"),
        "status": job.get("status"),
        "test_mode": job.get("test_mode", False),
        "total": len(clips),
        "clips": [
            {"number": c["number"], "title": c["title"], "status": c["status"],
             "video_url": c.get("video_url", "")}
            for c in clips
        ],
        "done": len(done),
        "failed": sum(1 for c in clips if c.get("status") in (FAILED, ABORTED)),
        "missing_scenes": job.get("missing_scenes", []),
        "final_video": job.get("final_video", ""),
        "assembly_mode": job.get("assembly_mode", "hard_cut"),
        "cost": {
            "spend_usd": round(job.get("spend_usd", 0.0), 2),
            "est_cost_usd": round(job.get("est_cost_usd", 0.0), 2),
            "remaining_budget_usd": remaining_budget(),
            "budget_usd": budget_usd(),
        },
        "observed_concurrency": job.get("observed_concurrency", 0),
        "error": job.get("error", ""),
    }


# ── The driver ─────────────────────────────────────────────────────────────

def _lock_for(job_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(job_id, threading.Lock())


def launch(job_id: str) -> None:
    """Start the driver in a daemon thread (no-op if one already holds the job)."""
    t = threading.Thread(target=drive_job, args=(job_id,), daemon=True, name=f"studio-{job_id[:8]}")
    t.start()


def drive_job(job_id: str) -> None:
    """Run the job to completion. Safe to call repeatedly / concurrently — the
    per-process lock and idempotent state make extra calls harmless."""
    lock = _lock_for(job_id)
    if not lock.acquire(blocking=False):
        return  # already being driven in this process
    try:
        _run(job_id)
    except Exception as exc:
        print(f"[JOB] driver crashed for {job_id}: {exc}")
        job = get_job(job_id)
        if job and job.get("status") not in JOB_TERMINAL:
            job["status"] = "failed"
            job["error"] = str(exc)[:300]
            save_job(job)
    finally:
        lock.release()


def _run(job_id: str) -> None:
    from core.media_manager import get_media_manager

    job = get_job(job_id)
    if not job or job["status"] in JOB_TERMINAL:
        return
    media = get_media_manager(job["user_id"])
    backoff = 1.0
    batch = submit_batch()
    # "capped" stops NEW submissions but never abandons clips already in flight —
    # they keep draining and we still assemble what succeeds. Survives resume via
    # the persisted flag.
    capped = bool(job.get("submission_capped"))

    while True:
        job = get_job(job_id)
        if not job or job["status"] in JOB_TERMINAL:
            return
        clips = job["clips"]

        # 1. Collect any running clips that have finished.
        for c in clips:
            if c["status"] == RUNNING:
                r = i2v.poll_clip(c["task_id"])
                if r["status"] == "SUCCEEDED":
                    _store_clip(media, job, c, r.get("video_url", ""))
                elif r["status"] == "FAILED":
                    c["status"] = FAILED
                    c["error"] = r.get("error", "clip failed")

        running = sum(1 for c in clips if c["status"] == RUNNING)
        pending = [c for c in clips if c["status"] == PENDING]

        # 2. Submit new clips up to the batch limit, honouring the budget cap.
        while pending and running < batch and not capped:
            if not job["test_mode"] and not _fake():
                if global_spent() + cost_per_clip() > budget_usd():
                    # Would cross the hard cap — stop submitting; the clips already
                    # running keep going and get assembled. Job is flagged capped.
                    capped = True
                    job["submission_capped"] = True
                    for rest in pending:
                        rest["status"] = ABORTED
                        rest["error"] = "budget cap reached"
                    print(f"[JOB] budget cap hit; aborting {len(pending)} unsubmitted clip(s)")
                    break
            c = pending.pop(0)
            if c.get("mode", "t2v") == "i2v" and c.get("image_ref"):
                sub = i2v.submit_clip(c["motion"], c["image_ref"],
                                      duration=job.get("duration"), tag=str(c["number"]),
                                      fake=bool(job.get("test_mode")))
            else:
                sub = i2v.submit_clip_t2v(c.get("prompt") or c.get("motion", ""),
                                          duration=job.get("duration"), tag=str(c["number"]),
                                          fake=bool(job.get("test_mode")))
            if sub.get("status_code") == 429:
                # Record the in-flight count at which Alibaba pushed back.
                job["observed_concurrency"] = running
                job["rate_limited_at"] = running
                save_job(job)
                backoff = min(backoff * 2, 60.0)
                print(f"[JOB] 429 at concurrency={running}; backing off {backoff}s")
                time.sleep(backoff)
                break
            if sub.get("error"):
                c["status"] = FAILED
                c["error"] = sub["error"]
                continue
            c["task_id"] = sub["task_id"]
            c["status"] = RUNNING
            running += 1
            if not job["test_mode"] and not _fake():
                add_spend(cost_per_clip())
                job["spend_usd"] = round(job.get("spend_usd", 0.0) + cost_per_clip(), 4)
            backoff = 1.0

        job["heartbeat"] = _now_iso()
        save_job(job)

        # 3. Terminate when every clip has reached a terminal state. Status stays
        #    "running" through draining so the top guard doesn't bail early.
        if all(c["status"] in _CLIP_TERMINAL for c in clips):
            break
        time.sleep(poll_interval())

    _finalize(job_id, media, capped)


def _store_clip(media, job: dict, clip: dict, url: str) -> None:
    """Pull a finished clip into permanent GridFS storage + the user's gallery."""
    try:
        rec = media.save_video(url, prompt=f"Scene {clip['number']}: {clip['title']}",
                               provider="AlibabaI2V", chat_id=f"studio_{job['user_id']}")
    except Exception as exc:
        rec = None
        print(f"[JOB] store clip {clip['number']} failed: {exc}")
    stored = rec["local_path"] if rec else ""
    if stored:
        clip["status"] = DONE
        clip["video_url"] = stored
    else:
        clip["status"] = FAILED
        clip["error"] = "could not store clip"


def _finalize(job_id: str, media, capped: bool = False) -> None:
    job = get_job(job_id)
    if not job:
        return
    clips = job["clips"]
    done = [c for c in clips if c["status"] == DONE and c.get("video_url")]
    job["missing_scenes"] = sorted(c["number"] for c in clips if c["status"] in (FAILED, ABORTED))

    if len(done) >= 2:
        final = _assemble(job["user_id"], done, media)
        if final.get("video_url"):
            job["final_video"] = final["video_url"]
            job["assembly_mode"] = final.get("mode", "hard_cut")
        else:
            job["error"] = final.get("error", "assembly failed")
    elif len(done) == 1:
        job["final_video"] = done[0]["video_url"]  # a single clip is the trailer

    if job["status"] == "running":
        job["status"] = "budget_capped" if capped else "done"
    job["heartbeat"] = _now_iso()
    save_job(job)
    _mirror_to_studio_run(job)


def _assemble(user_id: str, done_clips: list, media) -> dict:
    """Hard-cut concat of the finished clips (crossfade stays opt-in via
    STUDIO_ASSEMBLY_MODE). Pulls each clip out of GridFS, joins, stores."""
    import gridfs
    import shutil
    import tempfile

    import core.video_assembly as va
    from core.db import get_db

    if not va.available():
        return {"error": "ffmpeg not available"}

    mode = os.getenv("STUDIO_ASSEMBLY_MODE", "hard_cut").strip().lower()
    workdir = tempfile.mkdtemp(prefix="studio_job_asm_")
    local_paths = []
    try:
        db = get_db()
        bucket = gridfs.GridFSBucket(db) if db is not None else None
        from core.config import PROJECT_ROOT
        for idx, clip in enumerate(sorted(done_clips, key=lambda c: c.get("number", 0))):
            fname = clip["video_url"].rsplit("/", 1)[-1]
            data = None
            if bucket is not None:
                try:
                    data = bucket.open_download_stream_by_name(fname).read()
                except Exception:
                    data = None
            if data is None:
                disk = PROJECT_ROOT / clip["video_url"].lstrip("/")
                if disk.exists():
                    data = disk.read_bytes()
            if not data:
                continue
            dest = os.path.join(workdir, f"clip_{idx:03d}.mp4")
            with open(dest, "wb") as f:
                f.write(data)
            local_paths.append(dest)

        if len(local_paths) < 2:
            return {"error": "not enough clips to assemble"}
        out_path = os.path.join(workdir, "trailer.mp4")
        ok, err = va.assemble(local_paths, out_path, mode=mode)
        if not ok or not os.path.exists(out_path):
            return {"error": err or "assembly failed"}
        rec = media.save_media(out_path, media_type="video", prompt="Studio trailer",
                               provider="StudioAssembly", chat_id=f"studio_{user_id}")
        if not rec:
            return {"error": "could not store trailer"}
        return {"video_url": rec["local_path"], "mode": mode}
    except Exception as exc:
        print(f"[JOB] assembly crashed: {exc}")
        return {"error": str(exc)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _mirror_to_studio_run(job: dict) -> None:
    """Merge finished clips + trailer into the user's studio_runs doc so the
    Studio surface shows them after a reload (gallery is handled by save_video)."""
    coll = studio_runs_collection()
    if coll is None:
        return
    clips = [
        {"number": c["number"], "title": c["title"], "video_url": c["video_url"]}
        for c in job["clips"] if c["status"] == DONE and c.get("video_url")
    ]
    try:
        coll.update_one(
            {"_id": job["user_id"]},
            {"$set": {
                "clips": clips,
                "final_video": job.get("final_video", ""),
                "assembly_mode": job.get("assembly_mode", "hard_cut"),
                "missing_scenes": job.get("missing_scenes", []),
                "job_id": job["_id"],
                "job_status": job.get("status"),
            }},
            upsert=True,
        )
    except Exception as exc:
        print(f"[JOB] mirror to studio_run failed: {exc}")


def maybe_resume(job_id: str) -> None:
    """Called by the poll endpoint: if the job isn't finished and its driver
    looks dead (stale heartbeat), relaunch it. This is what makes a run survive
    the browser — and the process — going away."""
    job = get_job(job_id)
    if not job or job["status"] in JOB_TERMINAL:
        return
    hb = job.get("heartbeat")
    stale = True
    try:
        if hb:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds()
            stale = age > driver_stale_secs()
    except Exception:
        stale = True
    if stale:
        print(f"[JOB] resuming stale job {job_id}")
        launch(job_id)
