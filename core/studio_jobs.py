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
            cards: dict | None = None, logline: str = "", beats: list | None = None) -> dict:
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
            c["submitted_at"] = _now_iso()   # so run timing is diagnosable later
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
    """Pull a stored clip out of GridFS to a temp file so it can be watched."""
    import gridfs
    import tempfile as _tf
    from core.config import PROJECT_ROOT
    from core.db import get_db
    url = clip.get("video_url") or ""
    if not url:
        return ""
    fname = url.rsplit("/", 1)[-1]
    data = None
    try:
        db = get_db()
        if db is not None:
            data = gridfs.GridFSBucket(db).open_download_stream_by_name(fname).read()
    except Exception:
        data = None
    if data is None:
        disk = PROJECT_ROOT / url.lstrip("/")
        if disk.exists():
            data = disk.read_bytes()
    if not data:
        return ""
    path = os.path.join(_tf.gettempdir(), f"review_{fname}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def _review_clip(job: dict, clip: dict) -> None:
    """Marcus watches the clip he just got back and says whether it matches."""
    import core.video_vision as vv
    if not vv.available() or job.get("test_mode") or _fake():
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


def _finalize(job_id: str, media, capped: bool = False) -> None:
    job = get_job(job_id)
    if not job:
        return
    clips = job["clips"]
    done = [c for c in clips if c["status"] == DONE and c.get("video_url")]
    job["missing_scenes"] = sorted(c["number"] for c in clips if c["status"] in (FAILED, ABORTED))

    if len(done) >= 2:
        final = _assemble(job["user_id"], done, media, cards=job.get("cards") or {})
        if final.get("video_url"):
            job["final_video"] = final["video_url"]
            job["assembly_mode"] = final.get("mode", "hard_cut")
        else:
            job["error"] = final.get("error", "assembly failed")
    elif len(done) == 1:
        job["final_video"] = done[0]["video_url"]  # a single clip is the trailer

    # Elena watches the finished cut start to finish.
    if job.get("final_video"):
        try:
            _review_cut(job)
        except Exception as exc:
            print(f"[JOB] cut review crashed (non-fatal): {exc}")

    job["finalize_attempts"] = int(job.get("finalize_attempts", 0)) + 1
    if job["status"] == "running":
        if job.get("final_video"):
            job["status"] = "budget_capped" if capped else "done"
        else:
            # Clips exist but no trailer came out — that is a FAILURE, and the
            # Studio must say so instead of quietly leaving loose clips.
            job["status"] = "failed"
            if not job.get("error"):
                job["error"] = ("Could not join the clips into a trailer."
                                if done else "No clips were produced.")
    job["heartbeat"] = _now_iso()
    save_job(job)
    _mirror_to_studio_run(job)


def card_seconds() -> float:
    return _f("STUDIO_CARD_SECONDS", 1.5)


def cards_enabled() -> bool:
    return os.getenv("STUDIO_TITLE_CARDS", "1").strip().lower() not in ("0", "false", "no", "off")


def _build_sequence(local_paths: list, ordered_clips: list, cards: dict, workdir: str) -> list:
    """Interleave title cards with the shots.

    Each card is rendered as its OWN short clip matching the footage's codec
    params, so the final join stays a `-c copy` stream copy. Burning text over
    the video would force a full re-encode (the crossfade path peaked at 737MB).
    A card precedes the shot it introduces; the last card closes the piece.
    """
    import core.video_assembly as va

    if not cards or not cards_enabled() or not local_paths:
        return local_paths

    params = va.probe_params(local_paths[0])
    sequence, last_card = [], None
    for idx, (path, clip) in enumerate(zip(local_paths, ordered_clips)):
        text = cards.get(str(clip.get("number"))) or ""
        if text:
            card_path = os.path.join(workdir, f"card_{idx:03d}.mp4")
            ok, err = va.make_title_card(
                text, card_path, width=params["width"], height=params["height"],
                fps=params["fps"], seconds=card_seconds(), has_audio=params["has_audio"],
            )
            if ok:
                sequence.append(card_path)
                last_card = card_path
            else:
                print(f"[JOB] title card '{text[:30]}' failed: {err}")
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


def _assemble(user_id: str, done_clips: list, media, cards: dict | None = None) -> dict:
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
    ordered = sorted(done_clips, key=lambda c: c.get("number", 0))
    try:
        db = get_db()
        bucket = gridfs.GridFSBucket(db) if db is not None else None
        from core.config import PROJECT_ROOT
        for idx, clip in enumerate(ordered):
            # 1. Local cache written when the clip was stored — no network at all.
            cached = clip.get("cache_path") or ""
            if cached and os.path.exists(cached):
                local_paths.append(cached)
                continue
            # 2. Otherwise fall back to pulling it back out of GridFS.
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
        sequence = _build_sequence(local_paths, ordered, cards or {}, workdir)
        out_path = os.path.join(workdir, "trailer.mp4")
        ok, err = va.assemble(sequence, out_path, mode=mode)
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
