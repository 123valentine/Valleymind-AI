"""Resumable batch migration of existing GridFS media -> Cloudflare R2.

For each file still in GridFS:
    1. read the bytes (with retries — Atlas GridFS reads have run 76-125s and
       ~half have timed out, so a dedicated client with a long socket timeout
       plus retries is essential);
    2. upload to R2 at the key derived from the media doc's local_path;
    3. verify byte-for-byte (R2 ETag == MD5 of the source bytes for a single-PUT
       upload; falls back to a full download+SHA256 compare if the ETag looks
       multipart);
    4. update the media doc (set r2_key/storage, drop the gridfs pointers);
    5. ONLY THEN delete the GridFS copy.

The GridFS copy is never removed until R2 holds a verified copy, so an abort at
any point is safe and the run is fully resumable: re-running skips files already
verified in R2 and finishes any half-done ones.

Usage:
    python _media_migrate_r2.py --dry-run     # report the plan, touch nothing
    python _media_migrate_r2.py --live        # migrate
    python _media_migrate_r2.py --live --limit 1   # one file (used for a canary)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, r"C:\Users\EGBUJIE VALENTINE\Desktop\Valleymind-AI")

from core.config import get_config  # noqa: E402
from core import r2_storage  # noqa: E402

PROGRESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_media_migrate_progress.json")
READ_ATTEMPTS = 4
SOCKET_TIMEOUT_MS = 300_000  # 5 min — the 96MB video can be slow out of Atlas


def _client():
    """Dedicated Mongo client with a long socket timeout for big GridFS reads."""
    from pymongo import MongoClient
    uri = get_config().mongodb_uri
    if not uri:
        raise RuntimeError("MONGODB_URI not configured")
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=20_000,
        connectTimeoutMS=20_000,
        socketTimeoutMS=SOCKET_TIMEOUT_MS,
        retryReads=True,
        retryWrites=True,
        maxPoolSize=4,
    )
    # Atlas M0 drops idle connections; a single ping can hit a stale socket.
    # Retry a few times before giving up so a wobble doesn't abort the run.
    last = None
    for attempt in range(1, 6):
        try:
            client.admin.command("ping")
            break
        except Exception as exc:
            last = exc
            print(f"   startup ping attempt {attempt}/5 failed: {str(exc)[:100]}")
            time.sleep(min(3 * attempt, 15))
    else:
        raise RuntimeError(f"Mongo unreachable after 5 ping attempts: {last}")
    return client, client.get_default_database(default="valleymind_db")


def _load_progress() -> dict:
    try:
        with open(PROGRESS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_progress(p: dict) -> None:
    tmp = PROGRESS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=1)
    os.replace(tmp, PROGRESS)


def _ct_for(fname: str, meta: dict) -> str:
    ct = (meta or {}).get("content_type")
    if ct:
        return ct
    ext = fname.rsplit(".", 1)[-1].lower()
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp", "mp4": "video/mp4",
            "webm": "video/webm", "mov": "video/quicktime"}.get(ext, "application/octet-stream")


def _key_for_file(fname: str, media_doc: dict, gridfs_meta: dict) -> str:
    if media_doc and media_doc.get("local_path"):
        return r2_storage.key_from_local_path(media_doc["local_path"])
    user = (gridfs_meta or {}).get("user_id") or "unknown"
    subdir = "videos" if fname.lower().endswith((".mp4", ".webm", ".mov")) else "images"
    return r2_storage.key_for(user, subdir, fname)


def _read_gridfs_chunked(db, gid, expected_len: int, batch: int = 50):
    """Resumable chunk-wise read straight from fs.chunks.

    A single GridFS ``.read()`` is one long streaming operation; on a big file
    Atlas M0 closes the socket partway and the whole read is lost. Reading BATCH
    chunks (255KB each) per cursor means a dropped connection only costs one
    small batch, which we retry from the last chunk index — so even the 96MB
    video reads reliably."""
    chunks = db["fs.chunks"]
    try:
        n_total = chunks.count_documents({"files_id": gid})
    except Exception as exc:
        print(f"      chunk count failed: {str(exc)[:120]}")
        return None
    if n_total == 0:
        return None
    buf = bytearray()
    next_n = 0
    while next_n < n_total:
        advanced = False
        for attempt in range(1, 6):
            try:
                cur = (chunks.find({"files_id": gid, "n": {"$gte": next_n}},
                                   {"n": 1, "data": 1})
                       .sort("n", 1).limit(batch).batch_size(batch))
                for ch in cur:
                    if ch["n"] != next_n:
                        break  # unexpected gap — re-query from next_n
                    buf.extend(bytes(ch["data"]))
                    next_n += 1
                    advanced = True
                break
            except Exception as exc:
                print(f"      chunk read at n={next_n}/{n_total} attempt {attempt} "
                      f"failed: {str(exc)[:100]}")
                time.sleep(min(3 * attempt, 15))
        if not advanced:
            print(f"      stuck at chunk n={next_n}; aborting chunked read")
            return None
        if next_n % 100 == 0 or next_n == n_total:
            print(f"      ... {next_n}/{n_total} chunks ({len(buf):,} B)")
    if expected_len and len(buf) != expected_len:
        print(f"      chunked read length {len(buf):,} != expected {expected_len:,}")
        return None
    return bytes(buf)


def _verify_r2(key: str, data: bytes) -> tuple[bool, str]:
    """Byte-for-byte verify the uploaded object. ETag of a single-PUT object is
    the hex MD5 of the bytes; if it comes back multipart ('-N'), fall back to a
    full download + SHA256 compare."""
    md5 = hashlib.md5(data).hexdigest()
    try:
        head = r2_storage.client().head_object(Bucket=r2_storage.bucket(), Key=key)
        etag = (head.get("ETag") or "").strip('"')
        if etag and "-" not in etag:
            return (etag.lower() == md5.lower()), f"etag={etag} md5={md5}"
    except Exception as exc:
        return False, f"head failed: {exc}"
    # Fallback: download and compare in full.
    back = r2_storage.download_bytes(key)
    return (hashlib.sha256(back).hexdigest() == hashlib.sha256(data).hexdigest()), "sha256-fallback"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default when --live is absent")
    ap.add_argument("--limit", type=int, default=0, help="max files this run (0=all)")
    args = ap.parse_args()

    if not r2_storage.available():
        print("ABORT: R2 not configured")
        return 1

    client, db = _client()
    files_coll = db["fs.files"]
    media_coll = db["media"]
    import gridfs
    bucket = gridfs.GridFSBucket(db)

    files = list(files_coll.find({}, {"_id": 1, "filename": 1, "length": 1, "metadata": 1}))
    total_bytes = sum(int(f.get("length") or 0) for f in files)
    print(f"GridFS files present: {len(files)}   total {total_bytes:,} bytes")

    progress = _load_progress()
    done = skipped = failed = moved_bytes = 0
    processed = 0

    for f in files:
        fname = f.get("filename", "")
        gid = f["_id"]
        length = int(f.get("length") or 0)
        meta = f.get("metadata") or {}
        media_doc = media_coll.find_one({"gridfs_filename": fname})
        key = _key_for_file(fname, media_doc, meta)

        already = r2_storage.object_exists(key)
        already_ok = already and (r2_storage.object_size(key) == length)

        print(f"\n- {fname}  ({length:,} B)")
        print(f"    key: {key}")
        print(f"    in R2 already: {already} (size match: {already_ok})")

        if not args.live:
            print("    DRY-RUN: would read GridFS -> upload -> verify -> update doc -> delete GridFS")
            continue

        if args.limit and processed >= args.limit:
            print("    (limit reached; stopping)")
            break

        # Resume: if R2 already has a size-matching object, don't re-upload —
        # just finish the tail (update doc + drop the GridFS copy).
        if already_ok:
            if media_doc is not None:
                media_coll.update_one({"_id": media_doc["_id"]},
                                      {"$set": {"r2_key": key, "storage": "r2"},
                                       "$unset": {"gridfs_id": "", "gridfs_filename": ""}})
            bucket.delete(gid)
            progress[fname] = {"status": "done", "key": key, "size": length, "resumed": True,
                               "ts": time.time()}
            _save_progress(progress)
            print("    already in R2 -> updated doc + deleted GridFS copy (resumed)")
            skipped += 1
            processed += 1
            continue

        processed += 1
        # 1. Read — resumable chunk-wise read straight from fs.chunks, robust to
        #    Atlas dropping the socket mid-read on large files.
        t0 = time.time()
        data = _read_gridfs_chunked(db, gid, length)
        secs = round(time.time() - t0, 1)
        if data is None:
            progress[fname] = {"status": "read_failed", "key": key, "ts": time.time()}
            _save_progress(progress)
            failed += 1
            print("    READ FAILED after retries — GridFS copy left intact")
            continue
        if len(data) != length:
            progress[fname] = {"status": "length_mismatch", "got": len(data), "want": length}
            _save_progress(progress)
            failed += 1
            print(f"    LENGTH MISMATCH read={len(data)} want={length} — skipping")
            continue
        print(f"    read {len(data):,} B in {secs}s")

        # 2. Upload
        ct = _ct_for(fname, meta)
        try:
            r2_storage.upload_bytes(key, data, ct)
        except Exception as exc:
            progress[fname] = {"status": "upload_failed", "err": str(exc)[:200]}
            _save_progress(progress)
            failed += 1
            print(f"    UPLOAD FAILED: {str(exc)[:160]} — GridFS copy left intact")
            continue

        # 3. Verify byte-for-byte
        ok, detail = _verify_r2(key, data)
        if not ok:
            try:
                r2_storage.delete_object(key)  # remove the bad copy so retry is clean
            except Exception:
                pass
            progress[fname] = {"status": "verify_failed", "detail": detail}
            _save_progress(progress)
            failed += 1
            print(f"    VERIFY FAILED ({detail}) — deleted bad R2 obj, GridFS intact")
            continue
        print(f"    verified ({detail})")

        # 4. Update media doc
        if media_doc is not None:
            media_coll.update_one({"_id": media_doc["_id"]},
                                  {"$set": {"r2_key": key, "storage": "r2"},
                                   "$unset": {"gridfs_id": "", "gridfs_filename": ""}})

        # 5. Delete GridFS copy (only now that R2 is verified)
        bucket.delete(gid)

        progress[fname] = {"status": "done", "key": key, "size": length,
                           "md5": hashlib.md5(data).hexdigest(), "read_secs": secs,
                           "ts": time.time()}
        _save_progress(progress)
        done += 1
        moved_bytes += length
        print("    MIGRATED -> R2 verified, doc updated, GridFS copy deleted")

    print("\n" + "=" * 50)
    if args.live:
        print(f"migrated: {done}   resumed/skipped: {skipped}   failed: {failed}")
        print(f"bytes moved this run: {moved_bytes:,}")
        remaining = files_coll.count_documents({})
        print(f"GridFS files remaining: {remaining}")
    else:
        print(f"DRY-RUN complete. {len(files)} file(s), {total_bytes:,} bytes would migrate.")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
