import json
import os
from pathlib import Path

from core.config import PROJECT_ROOT
from core.db import get_db, get_db_manager


def migrate():
    db = get_db()
    if db is None:
        print("[MIGRATE] Cannot connect to MongoDB. Aborting.")
        return

    db_manager = get_db_manager()
    users_dir = PROJECT_ROOT / "memory_data" / "users"

    if not users_dir.exists():
        print("[MIGRATE] No users directory found at", users_dir)
        return

    migrated_users = 0
    migrated_chats = 0

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name

        for assistant_dir in user_dir.iterdir():
            if not assistant_dir.is_dir():
                continue
            assistant_name = assistant_dir.name

            lt_file = assistant_dir / "long_term.json"
            if lt_file.exists():
                with open(lt_file, "r", encoding="utf-8") as f:
                    lt_data = json.load(f)
                lt_data["_id"] = user_id
                db.long_term.replace_one({"_id": user_id}, lt_data, upsert=True)
                print(f"[MIGRATE] long_term uploaded for user '{user_id}' / '{assistant_name}'")
                migrated_users += 1

            chats_dir = assistant_dir / "chats"
            if chats_dir.exists():
                for chat_file in chats_dir.iterdir():
                    if not chat_file.is_file() or chat_file.suffix != ".json":
                        continue
                    chat_id = chat_file.stem
                    with open(chat_file, "r", encoding="utf-8") as f:
                        messages = json.load(f)
                    db_manager.background_chat_write(chat_id, messages)
                    print(f"[MIGRATE] Chat '{chat_id}' uploaded for user '{user_id}'")
                    migrated_chats += 1

    print(f"[MIGRATE] Migration complete: {migrated_users} users, {migrated_chats} chats.")


if __name__ == "__main__":
    migrate()
