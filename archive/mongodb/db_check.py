import pprint
import json
import os
import sys
from glob import glob

from core.db import get_db

sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)


def _load_local_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _find_latest_chat():
    """Find the most recently updated chat JSON across all users."""
    candidates = []

    # Global chat
    global_path = "memory_data/chats/marcus_main_chat.json"
    data = _load_local_json(global_path)
    if data and isinstance(data, list) and data:
        last_time = data[-1].get("time", "")
        candidates.append((last_time, {"_source": global_path, "messages": data}))

    # Per-user chats
    for path in glob("memory_data/users/*/marcus/chats/marcus_main_chat.json"):
        data = _load_local_json(path)
        if data and isinstance(data, list) and data:
            last_time = data[-1].get("time", "")
            candidates.append((last_time, {"_source": path, "messages": data}))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _find_latest_long_term():
    """Find the most recently updated long_term JSON across all users."""
    candidates = []
    for path in glob("memory_data/users/*/marcus/long_term.json"):
        data = _load_local_json(path)
        if data and isinstance(data, dict):
            candidates.append((path, data))

    if not candidates:
        return None
    # No timestamp field, just return the last one alphabetically
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def main():
    db = get_db()
    using_mongo = db is not None

    print("=" * 72)
    print("CONNECTION: MongoDB available"
          if using_mongo else "CONNECTION: MongoDB unavailable (using local JSON files)")
    print("=" * 72)

    # ── CHATS ────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("LAST DOCUMENT IN 'chats'")
    print("=" * 72)

    chat_doc = None
    if using_mongo:
        raw = db.chats.find_one(sort=[("_id", -1)])
        if raw:
            chat_doc = {"_source": "MongoDB", **raw}

    if chat_doc is None:
        chat_doc = _find_latest_chat()

    if chat_doc:
        pprint.pprint(chat_doc, indent=2, width=120)
    else:
        print("(empty)")

    # ── LONG_TERM ────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("LAST DOCUMENT IN 'long_term'")
    print("=" * 72)

    lt_doc = None
    if using_mongo:
        raw = db.long_term.find_one(sort=[("updated_at", -1)])
        if raw:
            lt_doc = {"_source": "MongoDB", **raw}

    if lt_doc is None:
        lt_doc = _find_latest_long_term()

    if lt_doc:
        pprint.pprint(lt_doc, indent=2, width=120)
    else:
        print("(empty)")

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
