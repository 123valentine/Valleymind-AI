"""Executor for the approved media deletion manifest.

DRY-RUN by default; pass --live to actually delete.

Safety design:
  * Operates ONLY on the 94 filenames in delete_filenames[].
  * Three hard gates refuse to run if a doomed filename (or its GridFS _id)
    overlaps a keeper. A keeper can never be deleted.
  * For each doomed filename it removes BOTH the GridFS file (fs.files +
    fs.chunks, via GridFSBucket.delete) and the matching `media` metadata doc.
  * Deletes are permitted even while the cluster is over quota; inserts/updates
    are not. This script performs no inserts/updates.
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, r"C:\Users\EGBUJIE VALENTINE\Desktop\Valleymind-AI")

from core.db import get_db  # noqa: E402
import gridfs  # noqa: E402

MANIFEST = r"C:\Users\EGBUJIE VALENTINE\Desktop\Valleymind-AI\_media_delete_manifest.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    with open(MANIFEST, encoding="utf-8") as f:
        m = json.load(f)
    doomed = list(dict.fromkeys(m["delete_filenames"]))  # dedupe, preserve order
    keepers = set(
        m.get("keep_source_clips", [])
        + m.get("keep_finished", [])
        + m.get("keep_images", [])
    )
    doomed_set = set(doomed)

    # ---- SAFETY GATE 1: no filename appears in both lists --------------------
    overlap = doomed_set & keepers
    if overlap:
        print(f"ABORT: {len(overlap)} filename(s) in BOTH doomed and keepers: {sorted(overlap)}")
        return 2

    db = get_db()
    if db is None:
        print("ABORT: Mongo unavailable (get_db returned None)")
        return 1
    files_coll = db["fs.files"]
    media_coll = db["media"]
    bucket = gridfs.GridFSBucket(db)

    doomed_files = list(files_coll.find({"filename": {"$in": doomed}}, {"_id": 1, "filename": 1, "length": 1}))
    doomed_bytes = sum(int(d.get("length") or 0) for d in doomed_files)
    doomed_media = media_coll.count_documents({"gridfs_filename": {"$in": doomed}})

    keeper_files = list(files_coll.find({"filename": {"$in": list(keepers)}}, {"_id": 1, "filename": 1}))
    keeper_present = {d["filename"] for d in keeper_files}
    keeper_missing = keepers - keeper_present

    # ---- SAFETY GATE 2: no shared GridFS _id between doomed and keepers ------
    doomed_ids = {d["_id"] for d in doomed_files}
    keeper_ids = {d["_id"] for d in keeper_files}
    id_overlap = doomed_ids & keeper_ids
    if id_overlap:
        print(f"ABORT: {len(id_overlap)} GridFS _id(s) shared between doomed and keepers")
        return 2

    print("=== DELETION PLAN ===")
    print(f"doomed filenames in manifest    : {len(doomed)}")
    print(f"  matching GridFS files         : {len(doomed_files)}")
    print(f"  matching media docs           : {doomed_media}")
    print(f"  bytes to free (sum of length) : {doomed_bytes:,}")
    print(f"  manifest total_delete_bytes   : {m.get('total_delete_bytes'):,}")
    print(f"keeper filenames                : {len(keepers)}")
    print(f"  present in GridFS             : {len(keeper_present)}")
    if keeper_missing:
        print(f"  !! keepers MISSING from GridFS : {sorted(keeper_missing)}")
    print()

    if not args.live:
        print("DRY-RUN — nothing deleted. Sample of files that WOULD be removed:")
        for d in doomed_files[:5]:
            print(f"   - {d['filename']}  ({int(d.get('length') or 0):,} B)  _id={d['_id']}")
        print(f"   ... and {max(0, len(doomed_files) - 5)} more GridFS files")
        print(f"Would also delete {doomed_media} media docs (gridfs_filename in doomed).")
        print(f"Keepers that stay untouched: {sorted(keeper_present)}")
        print("\nRe-run with --live to execute.")
        return 0

    # ---- LIVE --------------------------------------------------------------
    print("LIVE DELETE — doomed set only ...")
    gridfs_deleted = 0
    gridfs_errors: list[tuple[str, str]] = []
    for d in doomed_files:
        fn = d["filename"]
        if fn in keepers:  # GATE 3, per-item belt & suspenders
            print(f"   SKIP (keeper!) {fn}")
            continue
        try:
            bucket.delete(d["_id"])
            gridfs_deleted += 1
        except Exception as exc:
            gridfs_errors.append((fn, str(exc)))

    media_res = media_coll.delete_many({"gridfs_filename": {"$in": doomed}})

    print(f"GridFS files deleted : {gridfs_deleted} / {len(doomed_files)}")
    if gridfs_errors:
        print(f"GridFS delete errors : {len(gridfs_errors)}")
        for fn, e in gridfs_errors[:10]:
            print(f"   {fn}: {e[:160]}")
    print(f"media docs deleted   : {media_res.deleted_count}")
    print(f"approx bytes freed   : {doomed_bytes:,}")

    residual_doomed = files_coll.count_documents({"filename": {"$in": doomed}})
    residual_keepers = files_coll.count_documents({"filename": {"$in": list(keepers)}})
    print(f"residual doomed GridFS files : {residual_doomed}  (expect 0)")
    print(f"keeper GridFS files intact   : {residual_keepers}  (expect {len(keepers)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
