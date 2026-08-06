"""Delete the 6 source clips of Studio job 845da27b.

RUN THIS LAST — only after the recovered trailer is saved AND confirmed playing
on the live site. Dry-run by default; pass --live to delete.

Three guards refuse to run unless the trailer is safely in place:
  1. the job has a non-empty final_video,
  2. that final_video is NOT one of the six source clips,
  3. the trailer object actually exists in R2.
Each clip is removed via MediaManager.delete_media, which deletes both the R2
object and the `media` metadata doc.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, r"C:\Users\EGBUJIE VALENTINE\Desktop\Valleymind-AI")

from core import r2_storage as r2  # noqa: E402
from core import studio_jobs as sj  # noqa: E402
from core.media_manager import MediaManager  # noqa: E402

JOB_ID = "845da27b10c244da90652f24120f57bb"
SOURCE_CLIP_FILENAMES = {
    "df70cfa17bd94e6c8cf8407c0ef6c697.mp4",
    "3269428ad5344e0c8b4d4776fa63f2e6.mp4",
    "9a67011c22814933a4b1ff5cf4733659.mp4",
    "399ee7e7a8b54729bbd7a3468e912962.mp4",
    "ebc1ee3199c64e07ab4be4fcdb31d92d.mp4",
    "03812120fbc74f3ebf03f7bc1a11adbe.mp4",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    job = sj.get_job(JOB_ID)
    if not job:
        print("ABORT: job not found:", JOB_ID)
        return 1
    user_id = job["user_id"]
    final_video = job.get("final_video") or ""
    print(f"job {JOB_ID}  user={user_id}  status={job.get('status')}")
    print(f"final_video: {final_video!r}")

    # GUARD 1: trailer must be saved.
    if not final_video:
        print("ABORT (guard 1): job has no final_video — trailer not saved. Refusing.")
        return 1
    # GUARD 2: final_video must not itself be one of the source clips.
    final_fn = final_video.rsplit("/", 1)[-1]
    if final_fn in SOURCE_CLIP_FILENAMES:
        print(f"ABORT (guard 2): final_video is a source clip ({final_fn}). Refusing.")
        return 1
    # GUARD 3: trailer object must exist in R2.
    trailer_key = r2.key_from_local_path(final_video)
    if not r2.object_exists(trailer_key):
        print(f"ABORT (guard 3): trailer not found in R2 (key={trailer_key}). Refusing.")
        return 1
    print(f"guards passed — trailer verified in R2: {trailer_key} "
          f"({r2.object_size(trailer_key):,} B)\n")

    # Resolve the 6 clip media docs from the job's clip URLs.
    media = MediaManager(user_id)
    coll = media._media_collection()
    clip_urls = [c.get("video_url") for c in job.get("clips", []) if c.get("video_url")]

    targets = []  # (media_id, filename, r2_key)
    for url in clip_urls:
        fn = url.rsplit("/", 1)[-1]
        if fn not in SOURCE_CLIP_FILENAMES:
            print(f"   SKIP unexpected clip not in known source set: {fn}")
            continue
        doc = None
        if coll is not None:
            doc = (coll.find_one({"user_id": user_id, "local_path": url})
                   or coll.find_one({"user_id": user_id, "local_path": {"$regex": fn + "$"}}))
        if not doc:
            print(f"   (no media doc found for {fn}; may already be deleted)")
            continue
        targets.append((doc["_id"], fn, doc.get("r2_key")))

    print(f"clips to delete: {len(targets)}")
    for mid, fn, key in targets:
        print(f"   - {fn}  media_id={mid}  r2_key={key}")

    if not args.live:
        print("\nDRY-RUN — nothing deleted. Re-run with --live once the trailer is confirmed live.")
        return 0

    print("\nLIVE DELETE …")
    deleted = 0
    for mid, fn, _key in targets:
        if media.delete_media(mid):
            deleted += 1
            print(f"   deleted {fn}")
        else:
            print(f"   FAILED to delete {fn} (media_id={mid})")
    print(f"\ndeleted {deleted}/{len(targets)} source clips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
