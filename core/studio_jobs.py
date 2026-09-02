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
from datetime import datetime, timedelta, timezone

import core.video_i2v as i2v
from core.db import studio_jobs_collection, studio_runs_collection, video_spend_collection

# Clip states
PENDING, RUNNING, DONE, FAILED, ABORTED = "pending", "running", "done", "failed", "aborted"
_CLIP_TERMINAL = (DONE, FAILED, ABORTED)
# Job states
JOB_TERMINAL = ("done", "failed", "budget_capped", "aborted")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_watchdog_started = False


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


def default_duration() -> int:
    """Target runtime in seconds for a Studio piece. Short on purpose."""
    return max(5, _i("STUDIO_DEFAULT_DURATION", 30))


def seconds_per_scene() -> int:
    """How much finished runtime one scene is expected to carry. A shot plus its
    title card lands around 5s; packing scenes tighter turns the cut into a
    slideshow."""
    return max(2, _i("STUDIO_SECONDS_PER_SCENE", 5))


def scenes_for_duration(seconds: int) -> int:
    """Scene count derived from target runtime — ~1 scene per 5s, so a 30s
    piece gets about 6 scenes, not 18."""
    try:
        secs = max(5, int(seconds))
    except (TypeError, ValueError):
        secs = default_duration()
    return max(2, min(max_clips_cap(), round(secs / seconds_per_scene())))


def default_clips() -> int:
    """Scene count for the default runtime (kept for callers that want a count)."""
    return scenes_for_duration(default_duration())


def model_max_seconds() -> int:
    """Hard per-clip ceiling of the video model. wan2.7-t2v accepts an integer
    duration of 2–15s per single request (Alibaba Model Studio docs), so 15s is
    the longest a single clip can be. Env-overridable if the model changes."""
    return max(2, min(60, _i("STUDIO_MODEL_MAX_SECONDS", 15)))


def clip_seconds() -> int:
    """Target length of each generated clip. Default 10s: long enough that few
    clips are needed, short enough to stay inside the model's 15s cap and divide
    the common runtimes into whole clips (30/40/60/90 → 3/4/6/9). Clamped to the
    model's real maximum."""
    return max(2, min(model_max_seconds(), _i("STUDIO_CLIP_SECONDS", 10)))


def plan_clips(target_seconds: int) -> tuple[int, int]:
    """Split a requested runtime into (n_clips, seconds_per_clip), each clip at or
    under the model's single-request cap.

    This is what makes >15s videos possible at all: wan2.7-t2v renders at most 15s
    per request, so a longer piece is generated as several clips and joined by the
    assembly engine. A runtime that fits in one clip stays one clip (no assembly).
      10s → (1, 10)   ·   40s → (4, 10)   ·   90s → (9, 10)
    """
    try:
        target = max(2, int(target_seconds))
    except (TypeError, ValueError):
        target = default_duration()
    cap = model_max_seconds()
    if target <= cap:
        return 1, min(target, cap)
    base = clip_seconds()
    n = max(1, -(-target // base))              # ceil(target / base)
    per = max(2, min(cap, round(target / n)))
    return n, per


def test_clips() -> int:
    return _i("STUDIO_TEST_CLIPS", 3)


def max_clips_cap() -> int:
    """Absolute ceiling on clips per run (also bounds a 5-min trailer)."""
    return _i("STUDIO_MAX_CLIPS_CAP", 60)


def submit_batch() -> int:
    """How many clips may be in flight at once.

    Measured, not guessed: a true simultaneous burst of 8 submissions returned
    6x 200 and 2x 429, so Alibaba's concurrent-submission ceiling is ~6 (which
    is why batch 5 saw zero 429s). Default to 6 — a full default trailer
    submits at once, and larger runs keep 6 in flight and drain, avoiding 429
    storms entirely. The auto-backoff still catches anything above this."""
    return max(1, _i("STUDIO_SUBMIT_BATCH", 6))


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


def _clip_charge(job: dict) -> float:
    """$ charged for one video submission. A single-video generate job carries
    its own duration-scaled ``charge_usd`` (one long video ≈ several short clips
    worth of compute); a per-scene clip uses the flat per-clip estimate."""
    try:
        c = float(job.get("charge_usd") or 0.0)
    except (TypeError, ValueError):
        c = 0.0
    return c if c > 0 else cost_per_clip()


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
            mode: str = "t2v", sheet_text: str = "", look: str = "",
            cards: dict | None = None, logline: str = "", beats: list | None = None,
            per_clip: int | None = None) -> dict:
    """Build a multi-clip job over the scenes — used for generate-from-scratch
    runtimes longer than one clip can hold (each clip is joined by the assembly
    engine). Pass ``cards={}`` for a clean continuous cut with no title cards.

    ``mode`` picks the video path per run:
      * "t2v" (default) — text-to-video straight from the scene. Better prompt
        following, and no paid storyboard image is needed as the video source.
      * "i2v" — animate a reference image (user upload / reference mode). Falls
        back to t2v for any scene that has no image.

    ``per_clip`` sets each clip's generation length in seconds (≤ the model cap);
    when unset it falls back to the global VIDEO_CLIP_DURATION.
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
        "source": "generate",
        "test_mode": bool(test_mode),
        # Each clip is generated at per_clip seconds (≤ model cap); the whole is
        # joined by the assembly engine into one continuous video.
        "duration": int(per_clip) if per_clip else clip_duration(),
        "clips": clips,
        # {scene_number: card text} from Angelina's beats, cut in as their own
        # short clips at assembly (never burned over the footage).
        "cards": {str(k): str(v) for k, v in (cards or {}).items() if str(v).strip()},
        "logline": logline,
        "beats": beats or [],
        "cut_review": None,
        "vl_spend_usd": 0.0,
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


def new_upload_job(user_id: str, uploaded: list, *, cards: dict | None = None,
                   logline: str = "") -> dict:
    """Build a job to join footage the USER uploaded (not generated clips).

    The clips are already finished and stored, so they enter as ``DONE`` and the
    driver goes straight to the ONE assembly path — the exact same finalize,
    resume, and terminal-state guarantees as a generated trailer. Nothing is
    generated and nothing is charged here; assembly is free.

    ``uploaded``: ordered ``[{"video_url", "title"}]`` for the stored uploads.
    """
    clips = []
    for i, u in enumerate(uploaded, start=1):
        clips.append({
            "number": i,
            "title": (u.get("title") or f"Clip {i}")[:80],
            "mode": "upload",
            "motion": "", "prompt": "", "image_ref": "",
            "status": DONE,               # already shot — go straight to the join
            "task_id": "",
            "video_url": u.get("video_url", ""),
            "error": "",
        })
    job = {
        "_id": uuid.uuid4().hex,
        "user_id": user_id,
        "status": "running",
        "source": "upload",              # gates review off; labels the run
        "test_mode": False,
        "duration": None,
        "clips": clips,
        "cards": {str(k): str(v) for k, v in (cards or {}).items() if str(v).strip()},
        "logline": logline,
        "beats": [],
        "cut_review": None,
        "vl_spend_usd": 0.0,
        "final_video": "",
        "assembly_mode": "hard_cut",
        "spend_usd": 0.0,
        "cost_per_clip": 0.0,
        "est_cost_usd": 0.0,
        "missing_scenes": [],
        "observed_concurrency": 0,
        "rate_limited_at": None,
        "error": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "heartbeat": _now_iso(),
    }
    save_job(job)
    coll = studio_runs_collection()
    if coll is not None:
        try:
            coll.update_one({"_id": user_id},
                            {"$set": {"job_id": job["_id"], "job_status": "running"}},
                            upsert=True)
        except Exception as exc:
            print(f"[JOB] could not tag studio_run (upload): {exc}")
    return job


def new_single_video_job(user_id: str, *, prompt: str, duration: int | None,
                         mode: str = "t2v", image_ref: str = "", test_mode: bool = False,
                         logline: str = "", title: str = "", charge_usd: float = 0.0) -> dict:
    """GENERATE-FROM-SCRATCH: one Alibaba video for the WHOLE piece, displayed
    as-is — no chunking, no assembly, no title cards between shots.

    The entire piece is a SINGLE clip. The driver submits it once with the full
    target ``duration``, waits, stores it, and _finalize surfaces it directly
    (the single-clip finalize path never calls the assembly engine). Assembly is
    reserved for the upload-and-join path, which is a different feature.

    ``mode='i2v'`` with an ``image_ref`` animates one user-supplied image into the
    whole video; otherwise it is text-to-video straight from ``prompt``.
    """
    use_i2v = (mode == "i2v") and bool(image_ref)
    clip = {
        "number": 1,
        "title": (title or "Your video")[:80],
        "mode": "i2v" if use_i2v else "t2v",
        "motion": prompt,          # i2v path reads motion; t2v reads prompt
        "prompt": prompt,
        "image_ref": image_ref if use_i2v else "",
        "status": PENDING,
        "task_id": "",
        "video_url": "",
        "error": "",
    }
    charge = 0.0 if (test_mode or _fake()) else float(charge_usd or 0.0)
    job = {
        "_id": uuid.uuid4().hex,
        "user_id": user_id,
        "status": "running",
        "source": "generate",
        "single_video": True,          # one video, never assembled or carded
        "test_mode": bool(test_mode),
        # The FULL target runtime goes straight to Alibaba as one generation
        # (not the short per-clip VIDEO_CLIP_DURATION used by the chunked path).
        "duration": int(duration) if duration else None,
        "clips": [clip],
        "cards": {},                   # single video: no cards between shots
        "logline": logline,
        "beats": [],
        "cut_review": None,
        "vl_spend_usd": 0.0,
        "final_video": "",
        "assembly_mode": "single",
        "spend_usd": 0.0,
        "charge_usd": charge,          # what _run charges when the video submits
        "cost_per_clip": charge or (0.0 if (test_mode or _fake()) else cost_per_clip()),
        "est_cost_usd": charge,
        "missing_scenes": [],
        "observed_concurrency": 0,
        "rate_limited_at": None,
        "error": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "heartbeat": _now_iso(),
    }
    save_job(job)
    coll = studio_runs_collection()
    if coll is not None:
        try:
            coll.update_one({"_id": user_id},
                            {"$set": {"job_id": job["_id"], "job_status": "running"}},
                            upsert=True)
        except Exception as exc:
            print(f"[JOB] could not tag studio_run (single video): {exc}")
    return job


def new_autoedit_job(user_id: str, source_video_url: str, *, test_mode: bool = False,
                     instruction: str = "", voice_transcript: str = "",
                     voice_note: str = "", media_assets: list | None = None,
                     sticker_source: str = "", sticker_name: str = "",
                     sticker_pos: str = "br", manual: dict | None = None,
                     keep_plan: bool = False, prev_plan: dict | None = None) -> dict:
    """Massive Editing: auto-edit ONE uploaded video into a vertical short.

    The heavy work (transcribe → trim → B-roll → captions → render) is pure
    CPU/ffmpeg, so this reuses the studio job engine (background driver, atomic
    finalize claim, heartbeat, watchdog resume, durable terminal write) but runs
    the video_edit pipeline instead of clip generation. Nothing is charged for
    v1 (Groq Whisper + free B-roll provider).

    The user's instruction (typed and/or voice-transcribed), the original voice
    note (kept for review/debugging), any uploaded media assets and the selected
    sticker are all stored on the job so they reach the actual edit pipeline and
    influence the rendered video."""
    job = {
        "_id": uuid.uuid4().hex,
        "user_id": user_id,
        "status": "running",
        "source": "autoedit",
        "source_video": source_video_url,
        "test_mode": bool(test_mode),
        "instruction": str(instruction or "").strip(),
        "voice_transcript": str(voice_transcript or "").strip(),
        "voice_note": str(voice_note or ""),          # preserved original voice recording
        "media_assets": [
            {"type": str(a.get("type", "")), "url": str(a.get("url", "")),
             "name": str(a.get("name", ""))} for a in (media_assets or [])
        ],
        "sticker_source": str(sticker_source or ""),   # /static/... sticker URL
        "sticker_name": str(sticker_name or ""),
        "sticker_pos": str(sticker_pos or "br").lower() if str(sticker_pos or "br").lower()
                       in ("br", "bl", "tr", "tl", "center") else "br",
        "manual": manual or {},                         # professional sidebar overrides
        "keep_plan": bool(keep_plan),                   # replay prev AI plan
        "prev_plan": prev_plan or {},                   # the plan to replay
        "edit_plan": [],                                # AI Edit Plan checklist for the UI
        "clips": [],
        "final_video": "",
        "stats": None,
        "assembly_mode": "autoedit",
        "spend_usd": 0.0,
        "finalize_attempts": 0,
        "error": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "heartbeat": _now_iso(),
    }
    save_job(job)
    return job


def new_sticker_job(user_id: str, source_video_url: str, sticker_url: str,
                    *, position: str = "br") -> dict:
    """Apply a sticker/GIF to a video as a background job (one overlay re-encode),
    reusing the studio job engine for resume/heartbeat/durable-write."""
    job = {
        "_id": uuid.uuid4().hex,
        "user_id": user_id,
        "status": "running",
        "source": "sticker_overlay",
        "source_video": source_video_url,
        "sticker_url": sticker_url,
        "position": position or "br",
        "clips": [],
        "final_video": "",
        "assembly_mode": "sticker",
        "spend_usd": 0.0,
        "finalize_attempts": 0,
        "error": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "heartbeat": _now_iso(),
    }
    save_job(job)
    return job


def _run_sticker(job_id: str) -> None:
    """Drive a sticker-overlay job to a terminal state (heartbeat-pulsed)."""
    job = get_job(job_id)
    if not job or job.get("status") in JOB_TERMINAL:
        return
    if not _claim_finalize(job_id):
        print(f"[JOB] sticker {job_id[:8]} already claimed elsewhere; skipping")
        return
    stop = threading.Event()

    def _pulse():
        while not stop.wait(20.0):
            _touch_heartbeat(job_id)

    threading.Thread(target=_pulse, daemon=True, name=f"sticker-hb-{job_id[:8]}").start()
    try:
        import core.video_edit as ve
        result = ve.run_sticker_apply(job["user_id"], job.get("source_video", ""),
                                      job.get("sticker_url", ""), position=job.get("position", "br"),
                                      on_progress=lambda: _touch_heartbeat(job_id))
    finally:
        stop.set()

    job = get_job(job_id) or job
    if job.get("status") in JOB_TERMINAL:
        return
    job["assembling_at"] = None
    job["assembling_owner"] = None
    job["finalize_attempts"] = int(job.get("finalize_attempts", 0)) + 1
    if result.get("video_url"):
        job["final_video"] = result["video_url"]
        job["status"] = "done"
        job["error"] = ""
    else:
        job["status"] = "failed"
        job["error"] = result.get("error", "sticker overlay failed")
    job["heartbeat"] = _now_iso()
    save_job_durable(job)


def _run_autoedit(job_id: str) -> None:
    """Drive a Massive-Edit job to a terminal state. A background pulser keeps the
    heartbeat + finalize claim alive through the long ffmpeg passes so the
    watchdog never starts a duplicate render."""
    job = get_job(job_id)
    if not job or job.get("status") in JOB_TERMINAL:
        return
    if not _claim_finalize(job_id):
        print(f"[JOB] autoedit {job_id[:8]} already claimed elsewhere; skipping")
        return

    stop = threading.Event()

    def _pulse():
        while not stop.wait(20.0):
            _touch_heartbeat(job_id)

    threading.Thread(target=_pulse, daemon=True, name=f"edit-hb-{job_id[:8]}").start()

    def _save_plan(payload: dict, _nonce=[0]):
        """Persist the AI Edit Plan + progress onto the job so the frontend can
        show what the AI decided to do before/during rendering."""
        _nonce[0] += 1
        j = get_job(job_id) or job
        steps = (payload or {}).get("plan") or j.get("edit_plan") or []
        j["edit_plan"] = steps
        j["edit_plan_note"] = ((payload or {}).get("message") or "").strip()
        j["edit_plan_stage"] = ((payload or {}).get("stage") or "").strip()
        j["heartbeat"] = _now_iso()
        save_job(j)

    def _beat():
        _touch_heartbeat(job_id)

    try:
        import core.video_edit as ve
        stick = {}
        if job.get("sticker_source"):
            stick = {"url": job.get("sticker_source"),
                     "name": job.get("sticker_name") or "sticker",
                     "position": job.get("sticker_pos") or "br",
                     "kind": "sticker"}
        result = ve.run_autoedit(
            job["user_id"], job.get("source_video", ""),
            on_progress=_beat,
            test_mode=bool(job.get("test_mode")),
            instruction=job.get("instruction", ""),
            voice_transcript=job.get("voice_transcript", ""),
            media_assets=job.get("media_assets") or [],
            sticker=stick or None,
            manual=job.get("manual") or None,
            keep_plan=bool(job.get("keep_plan")),
            prev_plan=job.get("prev_plan") or None,
            on_plan=_save_plan)
    finally:
        stop.set()

    job = get_job(job_id) or job
    if job.get("status") in JOB_TERMINAL:
        return
    job["assembling_at"] = None
    job["assembling_owner"] = None
    job["finalize_attempts"] = int(job.get("finalize_attempts", 0)) + 1
    if result.get("video_url"):
        job["final_video"] = result["video_url"]
        job["stats"] = result.get("stats")
        job["status"] = "done"
        job["error"] = ""
    else:
        job["status"] = "failed"
        job["error"] = result.get("error", "auto-edit failed")
    job["heartbeat"] = _now_iso()
    save_job_durable(job)   # trailer is already in R2; don't lose the terminal write


def get_job(job_id: str) -> dict | None:
    coll = studio_jobs_collection()
    if coll is None:
        return None
    try:
        return coll.find_one({"_id": job_id})
    except Exception as exc:
        print(f"[JOB] read failed: {exc}")
        return None


def save_job(job: dict) -> bool:
    coll = studio_jobs_collection()
    if coll is None:
        return False
    job["updated_at"] = _now_iso()
    try:
        coll.replace_one({"_id": job["_id"]}, job, upsert=True)
        return True
    except Exception as exc:
        print(f"[JOB] save failed: {exc}")
        return False


def save_job_durable(job: dict, attempts: int = 5) -> bool:
    """Persist a job, retrying a transient Mongo failure with backoff.

    Used for the one write that must not be lost: the terminal "trailer is done"
    write in _finalize. The trailer bytes are already safe in R2 by then, so a
    dropped Mongo write here is exactly what strands a finished cut on "running"
    with an empty final_video (the failure _studio_recover_trailer.py cleaned up
    by hand). Retrying turns a brief Atlas write-block into a non-event."""
    for i in range(max(1, attempts)):
        if save_job(job):
            return True
        time.sleep(min(2.0 * (i + 1), 8.0))
    print(f"[JOB] durable save FAILED after {attempts} attempts for {job.get('_id')}")
    return False


def heartbeat_age(job: dict) -> float:
    """Seconds since the driver last reported progress (inf if never)."""
    hb = job.get("heartbeat")
    if not hb:
        return float("inf")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds()
    except Exception:
        return float("inf")


def public_view(job: dict) -> dict:
    """Trimmed state for the frontend + cost meter."""
    if not job:
        return {}
    clips = job.get("clips", [])
    age = heartbeat_age(job)
    # A non-terminal job whose driver has gone quiet is stalled, not working.
    # The instance sleeping on a free plan is the usual cause; surfacing it
    # beats spinning a progress label forever.
    stalled = job.get("status") not in JOB_TERMINAL and age > driver_stale_secs()
    done = [c for c in clips if c.get("status") == DONE]
    return {
        "job_id": job.get("_id"),
        "status": job.get("status"),
        "test_mode": job.get("test_mode", False),
        "source": job.get("source", "generated"),
        "single_video": bool(job.get("single_video")),
        "total": len(clips),
        "clips": [
            {"number": c["number"], "title": c["title"], "status": c["status"],
             "video_url": c.get("video_url", ""), "mode": c.get("mode", "t2v"),
             "review": c.get("review"),
             "mismatch": (c.get("review") or {}).get("match") == "no"}
            for c in clips
        ],
        "cut_review": job.get("cut_review"),
        "mismatches": [c["number"] for c in clips
                       if (c.get("review") or {}).get("match") == "no"],
        "done": len(done),
        "failed": sum(1 for c in clips if c.get("status") in (FAILED, ABORTED)),
        "missing_scenes": job.get("missing_scenes", []),
        "final_video": job.get("final_video", ""),
        "assembly_mode": job.get("assembly_mode", "hard_cut"),
        "stats": job.get("stats"),          # Massive Edit: trim/broll/caption counts
        "edit_plan": job.get("edit_plan"),  # Massive Edit: AI Edit Plan checklist
        "edit_plan_stage": job.get("edit_plan_stage", ""),
        "edit_plan_note": job.get("edit_plan_note", ""),
        "instruction": job.get("instruction", ""),
        "voice_transcript": job.get("voice_transcript", ""),
        "media_assets": job.get("media_assets") or [],
        "manual": job.get("manual") or {},
        "cost": {
            "spend_usd": round(job.get("spend_usd", 0.0), 2),
            "est_cost_usd": round(job.get("est_cost_usd", 0.0), 2),
            "remaining_budget_usd": remaining_budget(),
            "budget_usd": budget_usd(),
        },
        "observed_concurrency": job.get("observed_concurrency", 0),
        "error": job.get("error", ""),
        "stalled": bool(stalled),
        "heartbeat_age_s": None if age == float("inf") else round(age, 1),
        "can_retry_assembly": bool(
            job.get("status") in JOB_TERMINAL
            and not job.get("final_video")
            and sum(1 for c in clips if c.get("status") == DONE) >= 2
        ),
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
        job = get_job(job_id)
        if job and job.get("source") == "autoedit":
            _run_autoedit(job_id)          # Massive Editing pipeline
        elif job and job.get("source") == "sticker_overlay":
            _run_sticker(job_id)           # apply a sticker to a video
        else:
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
        charge = _clip_charge(job)
        while pending and running < batch and not capped:
            if not job["test_mode"] and not _fake():
                if global_spent() + charge > budget_usd():
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
            c["submitted_at"] = _now_iso()   # so run timing is diagnosable later
            running += 1
            if not job["test_mode"] and not _fake():
                add_spend(charge)
                job["spend_usd"] = round(job.get("spend_usd", 0.0) + charge, 4)
            backoff = 1.0

        job["heartbeat"] = _now_iso()
        save_job(job)

        # 3. Terminate when every clip has reached a terminal state. Status stays
        #    "running" through draining so the top guard doesn't bail early.
        if all(c["status"] in _CLIP_TERMINAL for c in clips):
            break
        time.sleep(poll_interval())

    _finalize(job_id, media, capped)


def vl_cost_per_1k() -> float:
    """Estimated $ per 1k Qwen3-VL tokens. Measured token counts are small
    (~1.8k for a 3s 720p clip, and the analysis proxy cuts that further), so
    review cost is a rounding error next to generation — but it is still
    charged against the same cap."""
    return _f("VL_COST_PER_1K_TOKENS", 0.002)


def _charge_vl(job: dict, tokens: int) -> None:
    if not tokens or job.get("test_mode") or _fake():
        return
    cost = round((tokens / 1000.0) * vl_cost_per_1k(), 6)
    if cost <= 0:
        return
    add_spend(cost)
    job["spend_usd"] = round(job.get("spend_usd", 0.0) + cost, 6)
    job["vl_spend_usd"] = round(job.get("vl_spend_usd", 0.0) + cost, 6)


def _local_copy_of_clip(clip: dict) -> str:
    """Pull a stored clip (R2 → GridFS → disk) to a temp file so it can be watched."""
    import tempfile as _tf
    from core.media_manager import fetch_media_bytes
    url = clip.get("video_url") or ""
    if not url:
        return ""
    data = fetch_media_bytes(url)
    if not data:
        return ""
    fname = url.rsplit("/", 1)[-1]
    path = os.path.join(_tf.gettempdir(), f"review_{fname}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def _review_clip(job: dict, clip: dict) -> None:
    """Marcus watches the clip he just got back and says whether it matches."""
    import core.video_vision as vv
    # Single-video generate jobs have exactly one clip that IS the whole video —
    # Elena's cut review covers it, so skip the per-shot Marcus review here.
    if not vv.available() or job.get("test_mode") or _fake() or job.get("single_video"):
        return
    path = _local_copy_of_clip(clip)
    if not path:
        return
    try:
        scene = {"action": clip.get("prompt", "") or clip.get("motion", ""),
                 "description": clip.get("title", "")}
        out = vv.marcus_clip_review(path, scene)
        if out.get("error"):
            print(f"[JOB] clip {clip['number']} review failed: {out['error']}")
            return
        clip["review"] = {"persona": "Marcus", "text": out.get("text", ""),
                          "match": out.get("match", "unknown")}
        _charge_vl(job, out.get("tokens", 0))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def cache_dir(job_id: str) -> str:
    """Per-job local scratch holding the clip files for assembly."""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"studio_cache_{job_id}")
    os.makedirs(path, exist_ok=True)
    return path


def _store_clip(media, job: dict, clip: dict, url: str) -> None:
    """Pull a finished clip into permanent GridFS storage + the user's gallery.

    The bytes are cached locally on the way through. Assembly then reads the
    cache instead of re-downloading tens of MB back out of Atlas GridFS — the
    round trip that made the final join slow and fragile enough to be killed
    mid-way on a free instance.
    """
    local = ""
    try:
        if url.startswith("http://") or url.startswith("https://"):
            import requests as _rq
            resp = _rq.get(url, timeout=180)
            resp.raise_for_status()
            local = os.path.join(cache_dir(job["_id"]), f"clip_{clip['number']:03d}.mp4")
            with open(local, "wb") as f:
                f.write(resp.content)
        elif os.path.exists(url):
            local = url
    except Exception as exc:
        print(f"[JOB] clip {clip['number']} local cache failed (will use provider URL): {exc}")
        local = ""

    try:
        rec = media.save_video(local or url, prompt=f"Scene {clip['number']}: {clip['title']}",
                               provider="AlibabaI2V", chat_id=f"studio_{job['user_id']}")
    except Exception as exc:
        rec = None
        print(f"[JOB] store clip {clip['number']} failed: {exc}")
    stored = rec["local_path"] if rec else ""
    if stored:
        clip["status"] = DONE
        clip["video_url"] = stored
        clip["completed_at"] = _now_iso()
        if local:
            clip["cache_path"] = local
        try:
            _review_clip(job, clip)   # Marcus watches it
        except Exception as exc:
            print(f"[JOB] review crashed (non-fatal): {exc}")
    else:
        clip["status"] = FAILED
        clip["error"] = "could not store clip"


def _touch_heartbeat(job_id: str) -> None:
    """Bump ONLY the heartbeat (and the assembly claim) with a targeted write, so
    the long finalize/assembly stretch keeps signalling "a driver is alive on
    this" without a full-document replace that could clobber a concurrent write.

    A fresh heartbeat here is what stops the stall detector from crying wolf
    mid-assembly AND stops a second worker (or the watchdog) from starting a
    duplicate join."""
    coll = studio_jobs_collection()
    if coll is None:
        return
    try:
        now = _now_iso()
        coll.update_one({"_id": job_id, "status": "running"},
                        {"$set": {"heartbeat": now, "assembling_at": now}})
    except Exception as exc:
        print(f"[JOB] heartbeat touch failed: {exc}")


def _claim_finalize(job_id: str) -> bool:
    """Atomically claim the right to finalize/assemble this job so two workers
    can never assemble the same clips at once. The claim is kept alive by the
    finalize heartbeat; it is considered abandoned (and re-claimable) once it
    goes stale, so a worker that dies mid-join never blocks recovery.

    Returns True if the claim is ours (proceed), False if someone else holds a
    live claim (skip — they are already on it)."""
    coll = studio_jobs_collection()
    if coll is None:
        return True  # no DB (local dev) — single process, nothing to race with
    ttl = max(180.0, driver_stale_secs() * 3)
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(seconds=ttl)).isoformat()
    owner = f"{os.getpid()}-{threading.get_ident()}"
    try:
        res = coll.find_one_and_update(
            {"_id": job_id, "status": "running",
             "$or": [{"assembling_at": {"$exists": False}},
                     {"assembling_at": None},
                     {"assembling_at": {"$lt": stale}}]},
            {"$set": {"assembling_at": now.isoformat(), "assembling_owner": owner}},
        )
        return res is not None
    except Exception as exc:
        print(f"[JOB] finalize claim check failed (proceeding): {exc}")
        return True


def _finalize(job_id: str, media=None, capped: bool = False) -> None:
    """Join the finished clips into one trailer, SAVE it, and drive the job to a
    terminal state — reliably, even if the process running this dies partway.

    The old finalize did everything (fetch → concat → upload → Elena's vision
    review) in one unbroken, heartbeat-free stretch and only wrote the result at
    the very end. A death anywhere in that window — a real risk when ffmpeg and a
    full VL pass spike memory on a 512MB box — stranded a finished trailer and
    left the job stuck on "running" forever. This version:
      * heartbeats through the whole stretch (live driver never looks dead);
      * persists the trailer + terminal status the INSTANT the join succeeds,
        BEFORE any review, so a finished cut is never lost;
      * always lands on done / failed / budget_capped — never "running".
    """
    job = get_job(job_id)
    if not job or job.get("status") in JOB_TERMINAL:
        return

    # Claim the join so a second worker / the watchdog can't duplicate it.
    if not _claim_finalize(job_id):
        print(f"[JOB] {job_id[:8]} finalize already claimed elsewhere; skipping")
        return
    _touch_heartbeat(job_id)  # a driver is on it, starting now

    clips = job["clips"]
    done = [c for c in clips if c["status"] == DONE and c.get("video_url")]

    final_url, final_mode, asm_err = "", "hard_cut", ""
    if len(done) >= 2:
        result = assemble_sources(
            job["user_id"],
            [{"number": c["number"], "title": c.get("title", ""),
              "video_url": c.get("video_url", ""), "cache_path": c.get("cache_path", "")}
             for c in done],
            cards=job.get("cards") or {},
            on_progress=lambda: _touch_heartbeat(job_id),
        )
        final_url = result.get("video_url", "")
        final_mode = result.get("mode", "hard_cut")
        asm_err = result.get("error", "")
    elif len(done) == 1:
        final_url = done[0]["video_url"]  # a single clip IS the video
        if job.get("single_video"):
            final_mode = "single"        # generated as one video, never assembled

    # PERSIST THE RESULT NOW — before Elena's review. Re-read first so we don't
    # clobber a clip that finished while we were joining.
    job = get_job(job_id) or job
    if job.get("status") in JOB_TERMINAL:
        return
    job["missing_scenes"] = sorted(
        c["number"] for c in job["clips"] if c["status"] in (FAILED, ABORTED))
    job["finalize_attempts"] = int(job.get("finalize_attempts", 0)) + 1
    job["assembling_at"] = None
    job["assembling_owner"] = None
    if final_url:
        job["final_video"] = final_url
        job["assembly_mode"] = final_mode
        job["error"] = ""
        job["status"] = "budget_capped" if capped else "done"
    else:
        # Clips exist but no trailer came out — that is a FAILURE, and the Studio
        # must say so plainly instead of hanging or leaving loose clips.
        job["status"] = "failed"
        job["error"] = asm_err or ("Could not join the clips into a trailer."
                                   if done else "No clips were produced.")
    job["heartbeat"] = _now_iso()
    # Durable: the trailer is already in R2; do not let a transient Mongo hiccup
    # leave the job stuck on "running" with an empty final_video.
    save_job_durable(job)
    _mirror_to_studio_run(job)

    # Review is OFF the critical path now: the job is already done and the
    # trailer is already saved and visible. Elena's take is a bonus.
    if job.get("final_video") and not job.get("cut_review") \
            and job.get("source") != "upload":
        try:
            _review_cut(job)          # fills job["cut_review"] in place
            save_job(job)
            _mirror_to_studio_run(job)
        except Exception as exc:
            print(f"[JOB] cut review crashed (non-fatal): {exc}")


def card_seconds() -> float:
    return _f("STUDIO_CARD_SECONDS", 1.5)


def card_fps() -> float:
    """Encoded framerate for a title card. A card is a static frame, so a low fps
    concatenates cleanly with the (higher-fps) clips — verified: -c copy keeps the
    correct total duration — and roughly halves the per-card encode. Bounded so a
    high clip fps can't make cards expensive again."""
    return max(1.0, _f("STUDIO_CARD_FPS", 12.0))


def card_build_budget() -> float:
    """Hard ceiling on TOTAL time spent rendering title cards for one trailer.

    Cards are an enhancement, not the trailer. On a throttled free instance six
    1080p libx264 encodes can run for minutes — the un-bounded stretch that got
    the finalize thread reaped before a trailer ever landed. Once this budget is
    spent we stop making cards and join the remaining shots raw, so assembly
    always completes with a real video out."""
    return _f("STUDIO_CARD_BUDGET_SECS", 120.0)


def cards_enabled() -> bool:
    return os.getenv("STUDIO_TITLE_CARDS", "1").strip().lower() not in ("0", "false", "no", "off")


def _build_sequence(local_paths: list, ordered_clips: list, cards: dict, workdir: str,
                    on_progress=None) -> list:
    """Interleave title cards with the shots.

    Each card is rendered as its OWN short clip matching the footage's codec
    params, so the final join stays a `-c copy` stream copy. Burning text over
    the video would force a full re-encode (the crossfade path peaked at 737MB).
    A card precedes the shot it introduces; the last card closes the piece.

    ``on_progress`` is pulsed after EVERY card. Card encoding is the one CPU-heavy
    stretch of assembly, and running it silently is what made the finalize thread
    look dead (stale heartbeat) on a throttled free instance and get reaped before
    a trailer ever landed. A pulse per card keeps the driver visibly alive.

    A single card that fails to render is skipped, never fatal — a trailer with
    one missing caption beats no trailer at all.
    """
    import core.video_assembly as va

    if not cards or not cards_enabled() or not local_paths:
        return local_paths

    def beat():
        if on_progress:
            try:
                on_progress()
            except Exception:
                pass

    params = va.probe_params(local_paths[0])
    fps = min(float(params["fps"] or 24.0), card_fps())
    budget = card_build_budget()
    start = time.monotonic()
    sequence, last_card, gave_up = [], None, False
    for idx, (path, clip) in enumerate(zip(local_paths, ordered_clips)):
        text = cards.get(str(clip.get("number"))) or ""
        # Stop making cards once the budget is spent — the rest of the shots still
        # get joined, so a slow box yields a trailer instead of a stuck job.
        if text and not gave_up and (time.monotonic() - start) > budget:
            gave_up = True
            print(f"[JOB] title-card budget ({budget:.0f}s) spent after {idx} card(s); "
                  "joining remaining shots without cards")
        if text and not gave_up:
            card_path = os.path.join(workdir, f"card_{idx:03d}.mp4")
            try:
                ok, err = va.make_title_card(
                    text, card_path, width=params["width"], height=params["height"],
                    fps=fps, seconds=card_seconds(), has_audio=params["has_audio"],
                )
            except Exception as exc:
                ok, err = False, str(exc)
            if ok:
                sequence.append(card_path)
                last_card = card_path
            else:
                print(f"[JOB] title card '{text[:30]}' failed (skipping): {err}")
            beat()  # a card just finished (or failed) — driver is alive
        sequence.append(path)
    # Close on the title beat.
    if last_card:
        sequence.append(last_card)
    return sequence


def _review_cut(job: dict) -> None:
    """Elena watches the assembled trailer and reports what she sees."""
    import core.video_vision as vv
    if not vv.available() or job.get("test_mode") or _fake():
        return
    path = _local_copy_of_clip({"video_url": job.get("final_video", "")})
    if not path:
        return
    try:
        out = vv.elena_cut_review(path, logline=job.get("logline", ""),
                                  beats=job.get("beats") or [])
        if out.get("error"):
            print(f"[JOB] cut review failed: {out['error']}")
            return
        job["cut_review"] = {"persona": "Elena", "text": out.get("text", "")}
        _charge_vl(job, out.get("tokens", 0))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def assemble_sources(user_id: str, sources: list, cards: dict | None = None,
                     mode: str | None = None, on_progress=None) -> dict:
    """THE one assembly path — join ordered video sources into a single video and
    store it in R2, returning ``{"video_url", "mode"}`` or ``{"error"}``.

    Shared by BOTH generated trailers (finalize) and user-UPLOADED footage, so
    there is a single, tested join with a single set of guarantees.

    ``sources``: ordered ``[{"number", "title", "video_url", optional
    "cache_path"}]``. Each source is read from its local cache if present (no
    network), else pulled from storage (R2 → GridFS → disk). A source that can't
    be fetched is reported by number — never silently dropped into a short cut.

    ``on_progress``: called between the heavy steps so the caller can keep a
    heartbeat fresh through a join that can run for minutes.
    """
    import shutil
    import tempfile

    import core.video_assembly as va
    from core.media_manager import get_media_manager, fetch_media_bytes

    if not va.available():
        return {"error": "the video assembler (ffmpeg) isn't available"}

    def beat():
        if on_progress:
            try:
                on_progress()
            except Exception:
                pass

    mode = (mode or os.getenv("STUDIO_ASSEMBLY_MODE", "hard_cut")).strip().lower()
    workdir = tempfile.mkdtemp(prefix="studio_asm_")
    ordered = sorted(sources, key=lambda c: c.get("number", 0))
    local_paths, fetch_failed = [], []
    try:
        for idx, clip in enumerate(ordered):
            # 1. Local cache written when the clip was stored — no network at all.
            cached = clip.get("cache_path") or ""
            if cached and os.path.exists(cached):
                local_paths.append(cached)
                beat()
                continue
            # 2. Otherwise pull it back out of storage (R2 → GridFS → disk).
            url = clip.get("video_url") or ""
            data = fetch_media_bytes(url) if url else None
            if not data:
                fetch_failed.append(clip.get("number"))
                beat()
                continue
            dest = os.path.join(workdir, f"clip_{idx:03d}.mp4")
            with open(dest, "wb") as f:
                f.write(data)
            local_paths.append(dest)
            beat()

        if len(local_paths) < 2:
            if fetch_failed:
                return {"error": f"couldn't fetch clip(s) {fetch_failed} for assembly"}
            return {"error": "not enough clips to assemble"}

        # Cards are an enhancement, never a gate: if building them raises, fall
        # back to joining the raw clips so a trailer always comes out.
        try:
            sequence = _build_sequence(local_paths, ordered, cards or {}, workdir,
                                       on_progress=on_progress)
        except Exception as exc:
            print(f"[JOB] title-card build failed wholesale ({exc}); joining raw clips")
            sequence = local_paths
        beat()
        out_path = os.path.join(workdir, "trailer.mp4")
        ok, err = va.assemble(sequence, out_path, mode=mode)
        beat()
        if not ok or not os.path.exists(out_path):
            return {"error": err or "assembly failed"}

        media = get_media_manager(user_id)
        rec = media.save_media(out_path, media_type="video", prompt="Studio trailer",
                               provider="StudioAssembly", chat_id=f"studio_{user_id}")
        if not rec:
            return {"error": "joined the clips but couldn't store the trailer"}
        beat()
        return {"video_url": rec["local_path"], "mode": mode}
    except Exception as exc:
        print(f"[JOB] assembly crashed: {exc}")
        return {"error": f"assembly error: {exc}"}
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
                "single_video": bool(job.get("single_video")),
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


def resume_stale_jobs(max_jobs: int = 25) -> int:
    """Relaunch every non-terminal job whose driver has gone quiet.

    This is the server-side safety net that makes a run finish even when the
    user has CLOSED THE BROWSER — recovery no longer depends on someone polling.
    Relaunching is safe: the per-process lock, the atomic finalize claim, and the
    terminal-status guards make a duplicate launch a no-op."""
    coll = studio_jobs_collection()
    if coll is None:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=driver_stale_secs())).isoformat()
    try:
        stale = list(coll.find(
            {"status": {"$nin": list(JOB_TERMINAL)},
             "$or": [{"heartbeat": {"$exists": False}},
                     {"heartbeat": None},
                     {"heartbeat": {"$lt": cutoff}}]},
            {"_id": 1}).limit(max_jobs))
    except Exception as exc:
        print(f"[JOB] watchdog scan failed: {exc}")
        return 0
    for d in stale:
        launch(d["_id"])
    if stale:
        print(f"[JOB] watchdog relaunched {len(stale)} stale job(s)")
    return len(stale)


def start_watchdog(interval: float | None = None) -> None:
    """Start the background sweeper once per process (idempotent). Set
    STUDIO_WATCHDOG=0 to disable it (e.g. a local dev run that should not touch
    production jobs)."""
    if os.getenv("STUDIO_WATCHDOG", "1").strip().lower() in ("0", "false", "no", "off"):
        print("[JOB] studio assembly watchdog disabled (STUDIO_WATCHDOG=0)")
        return
    global _watchdog_started
    with _locks_guard:
        if _watchdog_started:
            return
        _watchdog_started = True
    iv = interval if interval is not None else _f("STUDIO_WATCHDOG_INTERVAL", 30.0)

    def _loop():
        # A short initial delay lets the app finish booting before the first scan.
        time.sleep(min(iv, 15.0))
        while True:
            try:
                resume_stale_jobs()
            except Exception as exc:
                print(f"[JOB] watchdog loop error: {exc}")
            time.sleep(iv)

    threading.Thread(target=_loop, daemon=True, name="studio-watchdog").start()
    print(f"[JOB] studio assembly watchdog started (every {iv:.0f}s)")
