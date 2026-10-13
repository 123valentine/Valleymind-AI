"""Project + version persistence for the Music workstation.

A *project* is the persistent unit of work: title, artwork, prompt, lyrics,
generation settings, audio references, stems, timeline, versions and metadata.
Every AI generation becomes a new immutable **version**; the current project
state is never destroyed by generating again.

Storage is deliberately shared with the existing cloud-sync store
(``memory_data/music_projects/<user>/projects.json``) so the older
``/api/music/projects`` endpoints and this richer API see the same data — no
duplicate project system. Reads/writes are atomic and scoped to the
authenticated ``user_id`` only.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from core.config import PROJECT_ROOT

_DIR = PROJECT_ROOT / "memory_data" / "music_projects"
_SAFE = None  # injected from app (_safe_user_id) so hashing matches exactly
MAX_PROJECTS = 500
MAX_VERSIONS = 50


def configure(*, safe_user_id=None, projects_dir=None) -> None:
    """Wire the app's own user-id hasher and project directory (reuse, no dup)."""
    global _SAFE, _DIR
    if safe_user_id is not None:
        _SAFE = safe_user_id
    if projects_dir is not None:
        _DIR = projects_dir if callable(projects_dir) else Path(projects_dir)


def _fallback_safe_user_id(email: str) -> str:
    normalized = (email or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _user_key(user_id: str) -> str:
    fn = _SAFE or _fallback_safe_user_id
    try:
        return fn(str(user_id or ""))
    except Exception:  # noqa: BLE001
        return _fallback_safe_user_id(str(user_id or ""))


def _path(user_id: str) -> Path:
    base = _DIR() if callable(_DIR) else _DIR
    folder = Path(base) / _user_key(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "projects.json"


def _load(user_id: str) -> list:
    fpath = _path(user_id)
    try:
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [p for p in data if isinstance(p, dict)]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save(user_id: str, projects: list) -> None:
    fpath = _path(user_id)
    try:
        tmp = str(fpath) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(projects[:MAX_PROJECTS], f, indent=2, default=str)
        os.replace(tmp, fpath)
    except OSError as exc:  # noqa: BLE001
        print(f"[ERROR] Failed to save music projects: {exc}")


def _now() -> int:
    return int(time.time())


def _version_stub(project: dict) -> dict:
    """Snapshot the creative state of a project into an immutable version."""
    return {
        "id": "v_" + uuid.uuid4().hex[:12],
        "label": "",
        "created_at": _now(),
        "snapshot": {
            k: project.get(k)
            for k in ("title", "name", "brief", "prompt", "lyrics", "structure",
                      "genre", "subgenre", "mood", "energy", "bpm", "key",
                      "timeSignature", "duration", "mode", "vocal", "language",
                      "negativePrompt", "arrangement", "audio", "stems",
                      "timeline", "sections", "instruments")
        },
    }


def _ensure_defaults(project: dict) -> dict:
    project.setdefault("id", "mp_" + uuid.uuid4().hex[:14])
    project.setdefault("name", project.get("title") or "Untitled")
    project.setdefault("title", project.get("name") or "Untitled")
    project.setdefault("versions", [])
    project.setdefault("stems", [])
    project.setdefault("metadata", {})
    project.setdefault("createdAt", _now())
    project["updatedAt"] = project.get("updatedAt") or project["createdAt"]
    project.setdefault("savedAt", project["updatedAt"])
    return project


def list_projects(user_id: str) -> list:
    return _load(user_id)


def get_project(user_id: str, project_id: str) -> dict | None:
    pid = str(project_id or "")
    for p in _load(user_id):
        if str(p.get("id")) == pid:
            return p
    return None


def _write_project(user_id: str, project: dict) -> dict:
    projects = _load(user_id)
    for i, p in enumerate(projects):
        if str(p.get("id")) == str(project.get("id")):
            projects[i] = project
            break
    else:
        projects.insert(0, project)
    _save(user_id, projects)
    return project


def create_project(user_id: str, data: dict | None = None) -> dict:
    project = _ensure_defaults(dict(data or {}))
    # A brand-new project starts with v1 so nothing is ever un-versioned.
    if not project["versions"]:
        v = _version_stub(project)
        v["label"] = "Version 1"
        project["versions"] = [v]
    _write_project(user_id, project)
    return project


def update_project(user_id: str, project_id: str, patch: dict) -> dict | None:
    project = get_project(user_id, project_id)
    if project is None:
        return None
    incoming = dict(patch or {})
    # Versions are append-only through add_version; never let a generic PUT
    # silently wipe a user's history.
    incoming.pop("versions", None)
    project.update(incoming)
    project["updatedAt"] = _now()
    project["savedAt"] = project["updatedAt"]
    _write_project(user_id, project)
    return project


def delete_project(user_id: str, project_id: str) -> bool:
    projects = _load(user_id)
    remaining = [p for p in projects if str(p.get("id")) != str(project_id)]
    if len(remaining) == len(projects):
        return False
    _save(user_id, remaining)
    return True


def duplicate_project(user_id: str, project_id: str, *, name: str = "") -> dict | None:
    source = get_project(user_id, project_id)
    if source is None:
        return None
    clone = json.loads(json.dumps(source, default=str))
    clone["id"] = "mp_" + uuid.uuid4().hex[:14]
    clone["name"] = name or (str(source.get("name") or "Untitled") + " (Copy)")
    clone["title"] = clone["name"]
    clone["createdAt"] = _now()
    clone["updatedAt"] = clone["createdAt"]
    clone["savedAt"] = clone["updatedAt"]
    clone["versions"] = [_version_stub(clone)]
    clone["versions"][0]["label"] = "Version 1"
    _write_project(user_id, clone)
    return clone


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #

def list_versions(user_id: str, project_id: str) -> list | None:
    project = get_project(user_id, project_id)
    if project is None:
        return None
    return list(project.get("versions") or [])


def add_version(user_id: str, project_id: str, *, label: str = "",
                package: dict | None = None, note: str = "") -> dict | None:
    """Append a new immutable version capturing the current project state.

    ``package`` (an AI creative package, generation settings or audio
    descriptor) is merged into the snapshot so the exact inputs that produced a
    version can always be recovered.
    """
    project = get_project(user_id, project_id)
    if project is None:
        return None
    version = _version_stub(project)
    if isinstance(package, dict) and package:
        version["snapshot"].update(package)
    version["label"] = str(label or "") or f"Version {len(project.get('versions') or []) + 1}"
    version["note"] = str(note or "")
    versions = list(project.get("versions") or [])
    versions.append(version)
    project["versions"] = versions[-MAX_VERSIONS:]
    project["updatedAt"] = _now()
    project["savedAt"] = project["updatedAt"]
    _write_project(user_id, project)
    return {"project": project, "version": version}


def get_version(user_id: str, project_id: str, version_id: str) -> dict | None:
    for v in (list_versions(user_id, project_id) or []):
        if str(v.get("id")) == str(version_id):
            return v
    return None


def restore_version(user_id: str, project_id: str, version_id: str) -> dict | None:
    """Restore a version's snapshot as the current state.

    The pre-restore state is itself snapshotted first, so restoring never
    destroys work either.
    """
    project = get_project(user_id, project_id)
    if project is None:
        return None
    version = get_version(user_id, project_id, version_id)
    if version is None:
        return None
    before = _version_stub(project)
    before["label"] = "Autosave before restore"
    snapshot = dict(version.get("snapshot") or {})
    project.update(snapshot)
    versions = list(project.get("versions") or [])
    versions.append(before)
    project["versions"] = versions[-MAX_VERSIONS:]
    project["updatedAt"] = _now()
    project["savedAt"] = project["updatedAt"]
    _write_project(user_id, project)
    return project


def library(user_id: str, *, query: str = "", category: str = "") -> list:
    """Searchable / filterable library view over the user's projects."""
    projects = _load(user_id)
    q = (query or "").strip().lower()
    cat = (category or "").strip().lower()
    out = []
    for p in projects:
        if cat and cat not in ("all", "projects"):
            tags = [str(t).lower() for t in (p.get("tags") or [])]
            kind = str(p.get("mode") or "song").lower()
            if cat == "drafts" and not p.get("draft"):
                continue
            if cat == "favorites" and not p.get("favorite"):
                continue
            if cat == "instrumentals" and kind != "instrumental":
                continue
            if cat == "vocals" and kind == "instrumental":
                continue
            if cat in ("stems",) and not p.get("stems"):
                continue
            if cat == "exports" and not p.get("exports"):
                continue
            if cat not in ("drafts", "favorites", "instrumentals", "vocals",
                           "stems", "exports") and cat not in tags:
                continue
        if q and q not in str(p.get("name", "")).lower() \
                and q not in str(p.get("title", "")).lower() \
                and q not in str(p.get("genre", "")).lower() \
                and q not in str(p.get("mood", "")).lower():
            continue
        out.append(p)
    out.sort(key=lambda p: p.get("updatedAt") or p.get("savedAt") or 0, reverse=True)
    return out
