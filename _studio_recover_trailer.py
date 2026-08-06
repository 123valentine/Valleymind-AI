"""Recover & save the assembled trailer for Studio job 845da27b.

The lost session assembled the trailer, but Atlas was write-blocked, so it only
ever landed on local disk. This uploads that exact file to R2 and wires it into
the job + studio_run + gallery the way _finalize would have — WITHOUT
re-assembling (the six clips are already joined) and without re-running the
Elena vision review.

Idempotent: if the job already has a final_video it does nothing. Verifies the
bytes are in R2 (size match) BEFORE it marks the job done, so a half-upload can
never flip the job to "done" with a dead link.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, r"C:\Users\EGBUJIE VALENTINE\Desktop\Valleymind-AI")

from core import r2_storage as r2  # noqa: E402
from core import studio_jobs as sj  # noqa: E402
from core.media_manager import MediaManager  # noqa: E402

JOB_ID = "845da27b10c244da90652f24120f57bb"
TRAILER = r"C:\Users\EGBUJI~1\AppData\Local\Temp\studio_job_asm_4z1vb83s\trailer.mp4"


def main() -> int:
    if not os.path.exists(TRAILER):
        print("ABORT: trailer file missing:", TRAILER)
        return 1
    local_size = os.path.getsize(TRAILER)
    print(f"trailer file : {TRAILER}\n             ({local_size:,} B)")

    job = sj.get_job(JOB_ID)
    if not job:
        print("ABORT: job not found:", JOB_ID)
        return 1
    user_id = job["user_id"]
    print(f"job {JOB_ID}  user={user_id}")
    print(f"   status={job.get('status')}  final_video={job.get('final_video')!r}")

    if job.get("final_video"):
        print("Job already has a final_video — nothing to do (idempotent guard).")
        return 0

    # Snapshot the current studio_run pointer before we mirror over it, for the record.
    try:
        runs = sj.studio_runs_collection()
        cur = runs.find_one({"_id": user_id}) if runs is not None else None
        if cur:
            print(f"   current studio_run: job_id={cur.get('job_id')} status={cur.get('job_status')} "
                  f"clips={len(cur.get('clips') or [])} final_video={cur.get('final_video')!r}")
    except Exception as exc:
        print("   (could not read current studio_run:", str(exc)[:80], ")")

    # 1. Store the trailer bytes: R2 upload + media (gallery) doc, same as _assemble.
    media = MediaManager(user_id)
    rec = media.save_media(TRAILER, media_type="video", prompt="Studio trailer",
                           provider="StudioAssembly", chat_id=f"studio_{user_id}")
    if not rec:
        print("ABORT: save_media returned None — trailer NOT saved.")
        return 1
    print("saved media rec:", {k: rec.get(k) for k in
                               ("media_id", "local_path", "r2_key", "storage", "file_size")})

    # 2. Verify the bytes really landed in R2, byte-for-byte, before we touch the job.
    key = rec.get("r2_key")
    r2_size = r2.object_size(key) if key else None
    print(f"R2 verify: object_size({key}) = {r2_size}  (local {local_size})")
    if r2_size != local_size:
        print("ABORT: R2 size mismatch — trailer bytes NOT verified. Job left unchanged.")
        return 1

    # 3. Wire into the job exactly as _finalize would (status -> done, final_video set).
    job["final_video"] = rec["local_path"]
    job["assembly_mode"] = job.get("assembly_mode") or "hard_cut"
    job["finalize_attempts"] = int(job.get("finalize_attempts", 0)) + 1
    job["status"] = "done"
    job["heartbeat"] = sj._now_iso()
    sj.save_job(job)
    print("job updated: status=done, final_video set")

    # 4. Mirror to the user's studio_run so the Studio surface shows it after reload.
    sj._mirror_to_studio_run(job)
    print("studio_run mirrored")

    # 5. Read back from Mongo to confirm persistence.
    j2 = sj.get_job(JOB_ID)
    print(f"READBACK: job.status={j2.get('status')}  final_video={j2.get('final_video')!r}")
    print(f"\nServe URL (live site will 302 -> R2 presigned): {rec['local_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
