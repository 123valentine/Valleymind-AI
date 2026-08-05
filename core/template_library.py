"""Template Library — discovery + per-user template projects.

Discovery contract (future expansion): dropping a folder with template.json,
preview.mp4 and thumbnail.webp into static/templates/<id>/ is enough to make a
template appear — no code changes. Folders are scanned on the fly and cached
for a short TTL, so new templates auto-show.

Templates are reusable JSON projects, never finished videos. A user "uses" a
template to create a per-user project (memory_data/users/<user_id>/templates/)
with placeholder values; the render engine (core/template_render.py) swaps the
placeholders in and renders a video.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

from core.config import PROJECT_ROOT

TEMPLATES_DIR = PROJECT_ROOT / "static" / "templates"
PROJECTS_ROOT_TEMPLATE = str(PROJECT_ROOT / "memory_data" / "users" / "{user_id}" / "templates")
STATS_FILE = PROJECT_ROOT / "memory_data" / "template_stats.json"
STATIC_GENERATED = PROJECT_ROOT / "static" / "generated" / "templates"

SAFE_TID_RE = re.compile(r"^[a-zA-Z0-9_\-]{2,80}$")
SAFE_PID_RE = re.compile(r"^[a-zA-Z0-9_\-]{4,80}$")

_catalog_cache = {"at": 0.0, "items": []}
_CATALOG_TTL = 45.0
_stats_cache = {"at": 0.0, "data": None}
_STATS_TTL = 10.0

CARD_FIELDS = ("id", "name", "category", "tags", "description", "icon", "grad",
               "thumbnail", "preview", "duration", "aspect_ratio", "popularity",
               "likes", "downloads", "edit_time_min", "media_required",
               "media_slots", "width", "height", "fps", "has_preview")


# ── Stats ──────────────────────────────────────────────────────────────────

def _load_stats() -> dict:
    now = time.time()
    if _stats_cache["data"] is not None and (now - _stats_cache["at"]) < _STATS_TTL:
        return _stats_cache["data"]
    data: dict = {}
    try:
        if STATS_FILE.exists():
            data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
    except Exception as exc:
        print(f"[TEMPLATES] stats load failed: {exc}")
    _stats_cache.update(at=now, data=data)
    return data


def _save_stats(data: dict) -> None:
    try:
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(STATS_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATS_FILE)
        _stats_cache.update(at=time.time(), data=data)
    except Exception as exc:
        print(f"[TEMPLATES] stats save failed: {exc}")


def _stat_for(tid: str) -> dict:
    st = _load_stats().get(tid) or {}
    return {"likes": int(st.get("likes", 0)), "downloads": int(st.get("downloads", 0)),
            "liked_by": list(st.get("liked_by", []))}


def _set_stat(tid: str, patch: dict) -> None:
    data = _load_stats()
    entry = dict(data.get(tid) or {})
    entry.update(patch)
    data[tid] = entry
    _save_stats(data)


# ── Discovery ──────────────────────────────────────────────────────────────

def _scan_catalog() -> list:
    items = []
    if not TEMPLATES_DIR.is_dir():
        return items
    for folder in sorted(TEMPLATES_DIR.iterdir()):
        if not folder.is_dir():
            continue
        if not SAFE_TID_RE.match(folder.name):
            continue
        t = _read_template(folder.name)
        if t is None:
            continue
        items.append(t)
    items.sort(key=lambda t: t.get("popularity", 0), reverse=True)
    return items


def _read_template(tid: str) -> dict | None:
    folder = TEMPLATES_DIR / tid
    meta_path = folder / "template.json"
    if not meta_path.is_file():
        return None
    try:
        t = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[TEMPLATES] bad template.json for {tid}: {exc}")
        return None
    if not isinstance(t, dict):
        return None
    t["id"] = tid
    has_preview = (folder / "preview.mp4").is_file()
    t["has_preview"] = has_preview
    if not has_preview:
        t["preview"] = ""
    t["media_slots"] = sum(
        1 for p in (t.get("placeholders") or []) if p.get("type") in ("image", "video"))
    return t


def list_templates() -> list:
    now = time.time()
    if _catalog_cache["items"] and (now - _catalog_cache["at"]) < _CATALOG_TTL:
        return _catalog_cache["items"]
    items = _scan_catalog()
    _catalog_cache.update(at=now, items=items)
    return items


def refresh_catalog() -> None:
    _catalog_cache.update(at=0.0, items=[])


def card_view(t: dict) -> dict:
    stats = _stat_for(t["id"])
    out = {}
    for k in CARD_FIELDS:
        if k in t:
            out[k] = t[k]
    out["likes"] = int(t.get("likes", 0) or 0) + stats["likes"]
    out["downloads"] = int(t.get("downloads", 0) or 0) + stats["downloads"]
    return out


def list_cards() -> list:
    return [card_view(t) for t in list_templates()]


def get_template(tid: str) -> dict | None:
    if not SAFE_TID_RE.match(tid):
        return None
    for t in list_templates():
        if t["id"] == tid:
            return t
    t = _read_template(tid)
    if t:
        _catalog_cache["items"].append(t)
    return t


def categories() -> list:
    counts: dict = {}
    for t in list_templates():
        cat = t.get("category") or "Other"
        counts[cat] = counts.get(cat, 0) + 1
    return [{"name": name, "count": counts[name]} for name in sorted(counts)]


# ── Stats actions ──────────────────────────────────────────────────────────

def toggle_like(tid: str, user_id: str) -> dict:
    if not get_template(tid):
        return {"liked": False, "count": 0}
    st = _stat_for(tid)
    liked_by = st["liked_by"]
    if user_id in liked_by:
        liked_by.remove(user_id)
        liked = False
    else:
        liked_by.append(user_id)
        liked = True
    _set_stat(tid, {"liked_by": liked_by, "likes": len(liked_by)})
    st = _stat_for(tid)
    return {"liked": liked, "count": int(get_template(tid).get("likes", 0)) + st["likes"]}


def add_download(tid: str) -> int:
    st = _stat_for(tid)
    _set_stat(tid, {"downloads": st["downloads"] + 1})
    return int(get_template(tid).get("downloads", 0)) + st["downloads"] + 1


# ── Per-user projects ──────────────────────────────────────────────────────

def projects_root(user_id: str) -> Path:
    return Path(PROJECTS_ROOT_TEMPLATE.format(user_id=user_id))


def project_dir(user_id: str, pid: str) -> Path:
    return projects_root(user_id) / pid


def create_project(user_id: str, tid: str) -> tuple[dict, str]:
    t = get_template(tid)
    if t is None:
        return {}, "Template not found"
    pid = uuid.uuid4().hex[:16]
    root = project_dir(user_id, pid)
    root.mkdir(parents=True, exist_ok=True)
    placeholders = {}
    for p in t.get("placeholders") or []:
        key = p.get("key", "")
        if not key:
            continue
        entry = {
            "type": p.get("type", "text"),
            "required": bool(p.get("required", False)),
            "label": p.get("label", key),
            "value": "",
            "file": "",
        }
        placeholders[key] = entry
    project = {
        "_pid": pid,
        "template_id": tid,
        "name": t.get("name", tid),
        "aspect_ratio": t.get("aspect_ratio", "9:16"),
        "status": "draft",
        "progress": 0.0,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "placeholders": placeholders,
        "final_video": "",
        "error": "",
        "log": [],
    }
    _save_project(user_id, pid, project)
    return project, ""


def _save_project(user_id: str, pid: str, project: dict) -> None:
    root = project_dir(user_id, pid)
    root.mkdir(parents=True, exist_ok=True)
    project["updated_at"] = _now_iso()
    meta = dict(project)
    meta.pop("_pid", None)
    (root / "project.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def get_project(user_id: str, pid: str) -> dict | None:
    if not SAFE_PID_RE.match(pid):
        return None
    root = project_dir(user_id, pid)
    meta = root / "project.json"
    if not meta.is_file():
        return None
    try:
        p = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None
    p["_pid"] = pid
    return p


def save_project(user_id: str, pid: str, project: dict) -> None:
    _save_project(user_id, pid, project)


def save_project_media(user_id: str, pid: str, key: str, file_storage) -> str:
    """Persist an uploaded file for a placeholder. Returns the stored filename."""
    root = project_dir(user_id, pid)
    media_dir = root / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^A-Za-z0-9_\-]+", "_", key) or "asset"
    ext = os.path.splitext(file_storage.filename or "")[1][:10].lower() or ".bin"
    ext = re.sub(r"[^.\w]", "", ext)[:10]
    fname = f"{safe_key}{ext}"
    file_storage.save(str(media_dir / fname))
    return f"media/{fname}"


def media_path(user_id: str, pid: str, rel: str) -> Path:
    root = project_dir(user_id, pid).resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Invalid media path")
    return target


def list_user_projects(user_id: str) -> list:
    root = projects_root(user_id)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        p = get_project(user_id, folder.name)
        if not p:
            continue
        out.append({
            "id": p["_pid"],
            "name": p.get("name", folder.name),
            "template_id": p.get("template_id", ""),
            "status": p.get("status", ""),
            "created_at": p.get("created_at", ""),
            "updated_at": p.get("updated_at", ""),
            "final_video": p.get("final_video", ""),
        })
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return out


def delete_project(user_id: str, pid: str) -> bool:
    root = project_dir(user_id, pid)
    if not root.is_dir():
        return False
    import shutil
    shutil.rmtree(str(root), ignore_errors=True)
    return True


def project_public(user_id: str, p: dict) -> dict:
    return {
        "id": p["_pid"],
        "name": p.get("name", ""),
        "template_id": p.get("template_id", ""),
        "status": p.get("status", ""),
        "progress": round(float(p.get("progress", 0.0)), 3),
        "aspect_ratio": p.get("aspect_ratio", "9:16"),
        "error": p.get("error", ""),
        "log": p.get("log", [])[-20:],
        "final_video": p.get("final_video", ""),
        "created_at": p.get("created_at", ""),
        "updated_at": p.get("updated_at", ""),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
