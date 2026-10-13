"""ValleyMind task execution layer -- the SAFE, observable action engine.

This module is the authoritative validator and lifecycle model behind the
companion's "task" capability. It is deliberately PURE and small:

  * It NEVER executes generated code and NEVER runs shell commands. The only
    things that can happen are the explicitly enumerated, parameter-validated
    action types below -- and only after the user has granted the matching
    permission and (for consequential actions) explicitly confirmed.

  * It does NOT replace or weaken the privacy contract in static/cloud_vision.js
    (vision is still capture-only, permission-first, transient). Screen/active
    app inspection stays a read-only action gated behind the existing
    VMCloudVision permission; the browser-scoped interaction adapter lives in a
    SEPARATE module (static/task_controller.js) with its own permissions/tests.

  * It reuses the EXISTING brain (core.brain._call_llm_cluster) to turn a user
    request into a structured plan. If the brain is unavailable the planner
    falls back to a deterministic, side-effect-free heuristic so Pause/Stop and
    the lifecycle still work with no AI.

The browser executes the actions; this server side decides what a plan may
contain, what needs confirmation, and records outcomes (idempotently, and
without ever storing frames, secrets or raw screen content).
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid

# ── lifecycle ───────────────────────────────────────────────────────────────

TASK_STATES = (
    "idle",
    "planning",
    "awaiting_confirmation",
    "working",
    "paused",
    "completed",
    "failed",
    "cancelled",
)

TERMINAL_STATES = ("completed", "failed", "cancelled")

# Allowed lifecycle edges. Anything not listed is rejected (fail-closed).
VALID_TRANSITIONS = {
    "idle": ("planning",),
    "planning": ("awaiting_confirmation", "working", "completed", "failed", "cancelled"),
    "awaiting_confirmation": ("working", "completed", "failed", "cancelled"),
    "working": ("awaiting_confirmation", "paused", "completed", "failed", "cancelled"),
    "paused": ("working", "cancelled", "failed"),
    "completed": (),
    "failed": (),
    "cancelled": (),
}

# Map a task lifecycle state onto the EXISTING companion status vocabulary so
# the avatar/face controller (window.vmVrmCompanion.applyStatus) can be driven
# without inventing a parallel status system.
COMPANION_STATUS = {
    "idle": "idle",
    "planning": "thinking",
    "awaiting_confirmation": "listening",
    "working": "working",
    "paused": "idle",
    "completed": "happy",
    "failed": "confused",
    "cancelled": "idle",
}


# ── permissions ─────────────────────────────────────────────────────────────

# Every action that touches the browser/computer needs a granted permission.
# `respond` (the companion simply talking) is always allowed.
PERMISSIONS = ("vision", "scroll", "navigate", "click", "type")

# `navigate_external` is intentionally NOT in the default grantable set exposed
# by the UI; the server still recognises it so it can be enabled explicitly and
# audited, but only https origins are ever permitted.
ALL_PERMISSIONS = PERMISSIONS + ("navigate_external",)

DEFAULT_PERMISSIONS = ()


# ── action schema ───────────────────────────────────────────────────────────
# Each action declares: whether it is consequential (needs confirmation) and
# which permission it requires (None = always allowed).
ACTION_SPECS = {
    "respond": {"consequential": False, "permission": None},
    "inspect_view": {"consequential": False, "permission": "vision"},
    "capture_view": {"consequential": False, "permission": "vision"},
    "scroll": {"consequential": False, "permission": "scroll"},
    "navigate": {"consequential": False, "permission": "navigate"},
    "goto_workspace": {"consequential": False, "permission": "navigate"},
    "click": {"consequential": True, "permission": "click"},
    "type": {"consequential": True, "permission": "type"},
}

ACTION_TYPES = tuple(ACTION_SPECS.keys())

MAX_STEPS = 12
MAX_TEXT = 2000
MAX_LABEL = 120
MAX_GOAL = 200
MAX_SELECTOR = 200
MAX_URL = 2048

# In-app workspaces the companion is allowed to move the interface to. Mirrors
# the vmWsGo() allowlist in index.html; there is never an arbitrary target.
WORKSPACES = (
    "studio", "website", "sketch", "threed",
    "roundtable", "music", "editing", "analytics",
)

# Tokens that must never appear in a selector (defence-in-depth against the
# selector being used to reach script/eval surface).
_SELECTOR_BLOCKED = (
    "<", ">", "{", "}", "(", ")", ";", "`", "\\",
    "eval", "function", "javascript:", "onerror", "onload", "onclick",
    "document", "window", "constructor", "prototype", "__proto__",
)
_SELECTOR_ALLOWED = re.compile(r"^[A-Za-z0-9_\-.#\[\]=\"'*:\s]+$")
# A selector must be targeted (reference an id, class or attribute); bare tag
# names / universal selectors that could hit unexpected page chrome are refused.
_SELECTOR_TARGETED = re.compile(r"[#.\[]")

_URL_RELATIVE = re.compile(r"^/[^\s\\]*$")
_URL_ABSOLUTE = re.compile(r"^(https?)://([^\s/?#]+)([^\s]*)$", re.IGNORECASE)


class ActionValidationError(ValueError):
    """Raised when an action or its parameters fail validation."""


def _is_str(value) -> bool:
    return isinstance(value, str)


def _clean_text(value, limit=MAX_TEXT):
    text = str(value or "").strip()
    # Drop control characters but keep normal whitespace/newlines.
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
    return text[:limit]


def normalize_permissions(value):
    """Return a sorted tuple of the known permissions present in `value`."""
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    out = []
    for item in value:
        name = str(item or "").strip().lower()
        if name in ALL_PERMISSIONS and name not in out:
            out.append(name)
    return tuple(sorted(out))


def validate_url(url, allow_external=False):
    """Validate a navigation target.

    Relative app paths ("/foo") are always allowed. Absolute URLs are only
    allowed when `allow_external` is True AND the scheme is https with a host.
    javascript:, data:, file:, credentials-in-URL, and control characters are
    never allowed. Returns the normalized URL or raises ActionValidationError.
    """
    raw = str(url or "").strip()
    if not raw or len(raw) > MAX_URL:
        raise ActionValidationError("invalid url")
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in raw):
        raise ActionValidationError("invalid url characters")
    lowered = raw.lower()
    if lowered.startswith("//"):
        raise ActionValidationError("protocol-relative url is not allowed")
    if _URL_RELATIVE.match(raw):
        return raw
    if lowered.startswith(("javascript:", "data:", "file:", "vbscript:")):
        raise ActionValidationError("forbidden url scheme")
    match = _URL_ABSOLUTE.match(raw)
    if not match:
        raise ActionValidationError("url must be an app path or an absolute http(s) url")
    scheme = match.group(1).lower()
    host = match.group(2)
    if not allow_external:
        raise ActionValidationError("external navigation requires the navigate_external permission")
    if scheme != "https":
        raise ActionValidationError("only https external urls are allowed")
    if "@" in host:
        raise ActionValidationError("credentials in url are not allowed")
    return raw


def validate_workspace(name):
    """Validate an in-app workspace target against the fixed allowlist."""
    candidate = str(name or "").strip().lower()
    if candidate not in WORKSPACES:
        raise ActionValidationError("unknown workspace")
    return candidate


def validate_selector(selector):
    """Validate a CSS selector used to locate an authorized control/field."""
    sel = str(selector or "").strip()
    if not sel or len(sel) > MAX_SELECTOR:
        raise ActionValidationError("invalid selector")
    if not _SELECTOR_ALLOWED.match(sel):
        raise ActionValidationError("selector contains forbidden characters")
    low = sel.lower()
    for bad in _SELECTOR_BLOCKED:
        if bad in low:
            raise ActionValidationError("selector contains a forbidden token")
    if not _SELECTOR_TARGETED.search(sel):
        raise ActionValidationError("selector must target an id, class or attribute")
    return sel


def _coerce_int(value, low, high, default):
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


def normalize_action(raw):
    """Validate and normalize one raw action dict.

    Returns a dict with keys: type, params, consequential, permission.
    Raises ActionValidationError when the action cannot be safely represented.
    """
    if not isinstance(raw, dict):
        raise ActionValidationError("action must be an object")
    action_type = str(raw.get("type") or "").strip().lower()
    if action_type not in ACTION_SPECS:
        raise ActionValidationError(f"unsupported action type: {action_type or '(none)'}")
    spec = ACTION_SPECS[action_type]
    params_in = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    params = {}

    if action_type == "respond":
        text = _clean_text(params_in.get("text", raw.get("text", "")), MAX_TEXT)
        if not text:
            raise ActionValidationError("respond requires text")
        params["text"] = text

    elif action_type in ("inspect_view", "capture_view"):
        params["max_width"] = _coerce_int(
            params_in.get("max_width", raw.get("max_width")), 160, 1280, 720
        )

    elif action_type == "scroll":
        direction = str(params_in.get("direction", raw.get("direction", "down")) or "down").lower()
        if direction not in ("up", "down", "left", "right"):
            raise ActionValidationError("invalid scroll direction")
        params["direction"] = direction
        params["amount"] = _coerce_int(
            params_in.get("amount", raw.get("amount")), 1, 5000, 600
        )

    elif action_type == "navigate":
        params["url"] = validate_url(
            params_in.get("url", raw.get("url")), allow_external=False
        )

    elif action_type == "goto_workspace":
        params["workspace"] = validate_workspace(
            params_in.get("workspace", raw.get("workspace"))
        )
        label = _clean_text(params_in.get("label", raw.get("label", "")), MAX_LABEL)
        if label:
            params["label"] = label

    elif action_type == "click":
        params["selector"] = validate_selector(params_in.get("selector", raw.get("selector")))
        label = _clean_text(params_in.get("label", raw.get("label", "")), MAX_LABEL)
        if label:
            params["label"] = label

    elif action_type == "type":
        params["selector"] = validate_selector(params_in.get("selector", raw.get("selector")))
        text = _clean_text(params_in.get("text", raw.get("text", "")), MAX_TEXT)
        params["text"] = text
        label = _clean_text(params_in.get("label", raw.get("label", "")), MAX_LABEL)
        if label:
            params["label"] = label

    return {
        "type": action_type,
        "params": params,
        "consequential": bool(spec["consequential"]),
        "permission": spec["permission"],
    }


def normalize_steps(raw_steps, permissions=DEFAULT_PERMISSIONS):
    """Validate a list of raw steps; drop invalid ones (bounded).

    Returns (steps, dropped) where each step is a normalized, fully-described
    dict including a stable step_id, blocked flag and blocked_reason.
    Raises ActionValidationError if there are no valid steps at all.
    """
    if not isinstance(raw_steps, (list, tuple)):
        raise ActionValidationError("plan steps must be a list")
    permissions = normalize_permissions(permissions)
    steps = []
    dropped = 0
    for item in raw_steps[:MAX_STEPS * 2]:
        try:
            action = normalize_action(item)
        except ActionValidationError:
            dropped += 1
            continue
        if len(steps) >= MAX_STEPS:
            dropped += 1
            continue
        step = {
            "step_id": uuid.uuid4().hex[:12],
            "type": action["type"],
            "params": action["params"],
            "consequential": action["consequential"],
            "permission": action["permission"],
            "label": _clean_text(
                (item.get("label") if isinstance(item, dict) else "") or "",
                MAX_LABEL,
            ),
        }
        _apply_permission_gate(step, permissions)
        steps.append(step)
    if not steps:
        raise ActionValidationError("plan contains no valid steps")
    return steps, dropped


def _apply_permission_gate(step, permissions):
    """Mark a step blocked when its permission has not been granted."""
    permission = step.get("permission")
    if not permission:
        step["blocked"] = False
        step["blocked_reason"] = ""
        return
    if permission in permissions or (permission == "navigate" and "navigate_external" in permissions):
        step["blocked"] = False
        step["blocked_reason"] = ""
    else:
        step["blocked"] = True
        step["blocked_reason"] = f"permission required: {permission}"


def _finalize_plan(steps, goal, permissions, source, summary=""):
    permissions = normalize_permissions(permissions)
    plan = {
        "task_id": uuid.uuid4().hex,
        "goal": _clean_text(goal, MAX_GOAL),
        "summary": _clean_text(summary, MAX_LABEL),
        "source": source,
        "steps": steps,
        "requires_confirmation": any(s["consequential"] for s in steps),
        "blocked_permissions": sorted(
            {s["permission"] for s in steps if s.get("blocked") and s.get("permission")}
        ),
        "created_at": time.time(),
    }
    return plan


# ── planner: exact-JSON LLM first, deterministic heuristic fallback ─────────

def _extract_json(raw):
    """Extract the first balanced JSON object from a model response."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:idx + 1])
                except (ValueError, TypeError):
                    return None
    return None


PLANNER_SYSTEM = (
    "You are ValleyMind's task planner. Convert the user's request into a SHORT "
    "ordered plan of safe, browser-scoped steps. You do NOT run code and you do "
    "NOT control the operating system. Reply with ONLY a JSON object, no prose."
)

PLANNER_INSTRUCTIONS = """Return JSON shaped exactly like:
{
  "goal": "one short sentence",
  "summary": "one short sentence",
  "steps": [
    {"type": "respond", "params": {"text": "..."}, "label": "reply"},
    {"type": "inspect_view", "params": {"max_width": 720}, "label": "read the page"},
    {"type": "scroll", "params": {"direction": "down", "amount": 600}, "label": "scroll down"},
    {"type": "navigate", "params": {"url": "/some/app/path"}, "label": "open page"},
    {"type": "goto_workspace", "params": {"workspace": "studio"}, "label": "open the Studio"},
    {"type": "click", "params": {"selector": "#someButton", "label": "Submit"}, "label": "click Submit"},
    {"type": "type", "params": {"selector": "#someField", "text": "hello"}, "label": "type into field"}
  ]
}
Allowed step types: respond, inspect_view, capture_view, scroll, navigate, goto_workspace, click, type.
Rules:
- navigate url must be an app path starting with "/" (never an external site).
- goto_workspace workspace must be one of: studio, website, sketch, threed, roundtable, music, editing, analytics.
- selectors must target an id, class or attribute (e.g. "#send", ".card button", "[data-action]").
- Only use actions the user's granted permissions allow: {permissions}.
- Keep the plan tight (2-6 steps). Finish with a "respond" step that tells the user what happened.
"""


def plan_messages(request, permissions=DEFAULT_PERMISSIONS):
    perms = ", ".join(normalize_permissions(permissions)) or "(none granted yet)"
    return [
        {"role": "system", "content": PLANNER_SYSTEM},
        {
            "role": "user",
            "content": (
                PLANNER_INSTRUCTIONS.replace("{permissions}", perms)
                + "\nUser request: " + _clean_text(request, MAX_TEXT)
            ),
        },
    ]


def default_llm(messages):
    """Call the EXISTING multi-provider brain. Imported lazily so core.tasks
    stays import-light and tests never touch the network."""
    from core.brain import _call_llm_cluster
    text, _meta = _call_llm_cluster(messages, timeout=30)
    return text


_URL_IN_TEXT = re.compile(r"(https?://[^\s]+|/[A-Za-z0-9_\-./?#=&%]+)")


# Workspace keyword -> canonical id (id is always allowlist-validated before use).
# Ordered: longer/more-specific phrases first so "music studio" beats the bare
# "studio" keyword that is also present in the same sentence.
_WORKSPACE_HINTS = [
    ("valley studio", "studio"),
    ("ai builder", "website"),
    ("3d studio", "threed"),
    ("3-d", "threed"),
    ("three d", "threed"),
    ("massive editing", "editing"),
    ("council hall", "roundtable"),
    ("round table", "roundtable"),
    ("roundtable", "roundtable"),
    ("music studio", "music"),
    ("website", "website"),
    ("builder", "website"),
    ("sketch", "sketch"),
    ("3d", "threed"),
    ("studio", "studio"),
    ("council", "roundtable"),
    ("music", "music"),
    ("editing", "editing"),
    ("analytics", "analytics"),
]


def _detect_workspace(low_text):
    """Return a canonical workspace id when the request clearly asks to move to
    an in-app workspace (or None). Only used by the heuristic fallback planner."""
    # Needs an explicit "go/open/take me to/switch to/show me" style intent so a
    # stray word like "music" in a sentence doesn't hijack the interface.
    intent = (
        "take me to" in low_text or "go to" in low_text or "open the" in low_text
        or "open " in low_text or "navigate" in low_text or "switch to" in low_text
        or "show me the" in low_text or "bring up the" in low_text
    )
    if not intent:
        return None
    for keyword, canonical in _WORKSPACE_HINTS:
        if keyword in low_text:
            return canonical
    return None


def heuristic_plan(request, permissions=DEFAULT_PERMISSIONS):
    """Deterministic, side-effect-free plan used when the brain is unavailable.

    It only ever emits inspect_view / scroll / navigate / goto_workspace /
    respond steps -- never click or type, which require a model's page
    understanding and an explicit confirmation anyway.
    """
    text = _clean_text(request, MAX_TEXT)
    low = text.lower()
    steps = []

    url_match = _URL_IN_TEXT.search(text)
    if url_match and ("open" in low or "go to" in low or "navigate" in low or "visit" in low):
        try:
            target = validate_url(url_match.group(1), allow_external=False)
            steps.append({"type": "navigate", "params": {"url": target}, "label": "open page"})
        except ActionValidationError:
            pass

    workspace = _detect_workspace(low)
    if workspace:
        try:
            target = validate_workspace(workspace)
            steps.append({
                "type": "goto_workspace",
                "params": {"workspace": target, "label": "open the workspace"},
                "label": f"open {target} workspace",
            })
        except ActionValidationError:
            pass

    if "scroll" in low:
        direction = "up" if " up" in low or "top" in low else "down"
        amount_match = re.search(r"(\d{2,5})", text)
        amount = int(amount_match.group(1)) if amount_match else 600
        steps.append({
            "type": "scroll",
            "params": {"direction": direction, "amount": amount},
            "label": f"scroll {direction}",
        })

    if "read" in low or "look at" in low or "see" in low or "inspect" in low or "screen" in low:
        steps.append({"type": "inspect_view", "params": {"max_width": 720}, "label": "read the page"})

    steps.append({
        "type": "respond",
        "params": {"text": (
            "I can take this on with my full planner once the AI brain is reachable. "
            "For now I can scroll, open an app page you name, or read the page with "
            "your permission. Request noted: " + (text[:160] or "…")
        )},
        "label": "reply",
    })
    normalized, _dropped = normalize_steps(steps, permissions)
    return _finalize_plan(normalized, text, permissions, source="heuristic")


def build_plan(request, permissions=DEFAULT_PERMISSIONS, llm=None):
    """Build a validated plan from a user request.

    Tries the LLM planner first (injected via `llm`, defaulting to the existing
    brain), validates everything it returns, and falls back to the deterministic
    heuristic on ANY failure so the companion always has a safe, executable plan.
    """
    request_text = _clean_text(request, MAX_TEXT)
    if not request_text:
        raise ActionValidationError("request is required")
    permissions = normalize_permissions(permissions)
    llm = llm or default_llm

    try:
        raw = llm(plan_messages(request_text, permissions))
        parsed = _extract_json(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict):
            steps, _dropped = normalize_steps(parsed.get("steps"), permissions)
            goal = parsed.get("goal") or request_text
            summary = parsed.get("summary") or ""
            return _finalize_plan(steps, goal, permissions, source="llm", summary=summary)
    except ActionValidationError:
        pass
    except Exception as exc:  # provider outage / parse failure -> safe fallback
        print(f"[TASKS] planner LLM unavailable ({type(exc).__name__}); using heuristic plan")

    return heuristic_plan(request_text, permissions)


# ── lifecycle transition helper ─────────────────────────────────────────────

def apply_transition(current, nxt):
    """Return `nxt` if `current -> nxt` is a legal lifecycle edge, else raise."""
    current = str(current or "idle")
    nxt = str(nxt or "")
    if current not in VALID_TRANSITIONS or nxt not in TASK_STATES:
        raise ValueError(f"unknown task state: {current} -> {nxt}")
    if nxt not in VALID_TRANSITIONS[current]:
        raise ValueError(f"illegal task transition: {current} -> {nxt}")
    return nxt


# ── idempotency + audit store (in-memory, per-user, no secrets/frames) ──────

_AUDIT_LOCK = threading.Lock()
_AUDIT = {}          # user_id -> list[record]
_PLAN_CACHE = {}     # request_id -> plan
_MAX_RECORDS = 60
_MAX_PLAN_CACHE = 256
_OUTCOMES = ("ok", "failed", "skipped", "blocked", "denied")


def reset_store():
    """Clear the in-memory idempotency/audit store (used by tests)."""
    with _AUDIT_LOCK:
        _AUDIT.clear()
        _PLAN_CACHE.clear()


def cache_plan(request_id, plan):
    request_id = str(request_id or "").strip()[:64]
    if not request_id or not isinstance(plan, dict):
        return
    with _AUDIT_LOCK:
        if len(_PLAN_CACHE) >= _MAX_PLAN_CACHE:
            # drop the oldest insertion
            _PLAN_CACHE.pop(next(iter(_PLAN_CACHE)), None)
        _PLAN_CACHE[request_id] = copy.deepcopy(plan)


def get_cached_plan(request_id):
    request_id = str(request_id or "").strip()[:64]
    if not request_id:
        return None
    with _AUDIT_LOCK:
        plan = _PLAN_CACHE.get(request_id)
    return copy.deepcopy(plan) if plan is not None else None


def record_outcome(user_id, task_id, step_id, state, outcome, message=""):
    """Record a task/step outcome idempotently. Never stores frames or secrets.

    Repeats of the same (task_id, step_id, state, outcome) are de-duplicated so a
    retried request cannot double-count execution.
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    task_id = str(task_id or "").strip()[:64]
    step_id = str(step_id or "").strip()[:64]
    state = str(state or "").strip()
    if state not in TASK_STATES:
        raise ValueError(f"unknown task state: {state}")
    outcome = str(outcome or "").strip().lower()
    if outcome not in _OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome}")
    record = {
        "task_id": task_id,
        "step_id": step_id,
        "state": state,
        "outcome": outcome,
        "message": _clean_text(message, MAX_LABEL),
        "ts": time.time(),
    }
    with _AUDIT_LOCK:
        records = _AUDIT.setdefault(user_id, [])
        for existing in records:
            if (existing["task_id"] == task_id and existing["step_id"] == step_id
                    and existing["state"] == state and existing["outcome"] == outcome):
                return {"status": "success", "deduped": True, "record": copy.deepcopy(existing)}
        records.append(record)
        if len(records) > _MAX_RECORDS:
            del records[:len(records) - _MAX_RECORDS]
    return {"status": "success", "deduped": False, "record": copy.deepcopy(record)}


def get_tasks(user_id, limit=20):
    """Return the most recent recorded outcomes for a user (newest first)."""
    user_id = str(user_id or "").strip()
    limit = max(1, min(100, int(limit or 20)))
    with _AUDIT_LOCK:
        records = list(_AUDIT.get(user_id, []))
    return copy.deepcopy(records[-limit:][::-1])


def companion_status_for(state):
    """Map a task state onto the companion's applyStatus vocabulary."""
    return COMPANION_STATUS.get(str(state or "").strip(), "idle")
