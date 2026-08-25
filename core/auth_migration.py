"""Unified authentication core for ValleyMind.

Single source of truth for the question "may this account use the app?" and a
safe, idempotent migration that normalizes every stored account to the unified
schema.

UNIFIED RULES
-------------
* Every registration method (email/password, Google, anything future) creates
  an UNVERIFIED ValleyMind account first.
* Normal application access requires ``email_verified is True`` on the user
  record -- set ONLY by successfully completing a ValleyMind email OTP.
* Missing / NULL / wrong-type verification state means UNVERIFIED. It is never
  interpreted as verified.
* Existing accounts already marked verified at migration time are
  grandfathered (verified stays verified); everything else is explicitly
  normalized to False.

DRY-RUN MODE
-------------
``normalize_user_records(coll, dry_run=True)`` inspects every record and
reports what WOULD change without writing anything.  The ``changes`` list in
the returned stats dict gives per-record detail: the ``_id``, what field
changed, the old value, and the new value.  Pass ``dry_run=False`` (the
default) to actually apply the changes.

DELETED RECORDS
---------------
Records whose ``_id`` is in ``DELETE_IDS`` are removed during migration.
This allows accounts that need a clean start (e.g. malformed duplicates) to
be deleted so the user can re-register fresh.

This module is intentionally dependency-free (no Flask, no Mongo imports) so
it can be unit-tested with a fake collection.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

# Legacy security-question reset path was removed from the codebase; these
# residual fields are stripped from records during migration.
LEGACY_FIELDS = ("security_question", "security_answer_hash")

_TRUTHY_STRINGS = {"true", "1", "yes", "on"}

# Records deleted during migration -- accounts that need a clean start.
# These _ids are REMOVED by normalize_user_records so the user can re-register.
DELETE_IDS: Set[str] = {
    "dprinceonwuka@gmail.com",
}


def is_verified_record(record: object) -> bool:
    """Strictly True only when the record exists AND email_verified is True.

    Missing records, missing fields, None, False, and string quirks are all
    UNVERIFIED.  This is the ONLY predicate the app should use to grant
    normal application access.
    """
    if not isinstance(record, dict):
        return False
    value = record.get("email_verified")
    return value is True


def _coerce_verified(value: object) -> bool | None:
    """Map a legacy stored value to True / False, or None when it's already
    canonical False/absent and needs no write."""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in _TRUTHY_STRINGS:
        return True  # defensive: old string booleans keep their meaning
    if value is False:
        return None  # canonical; nothing to do
    return False  # missing / None / numbers / anything else -> explicit False


def _compute_changes(doc: dict) -> List[dict]:
    """Compute the list of field-level changes that normalize_user_records
    would make to *doc*.  Returns a list of dicts:
    ``[{"field": "...", "old": ..., "new": ...}, ...]``
    An empty list means the record is already canonical.
    """
    changes: List[dict] = []

    # -- Verification state --
    verdict = _coerce_verified(doc.get("email_verified"))
    if verdict is True and doc.get("email_verified") is not True:
        changes.append({
            "field": "email_verified",
            "old": repr(doc.get("email_verified")),
            "new": True,
            "reason": "string/truthy verification coerced to True",
        })
    elif verdict is False:
        changes.append({
            "field": "email_verified",
            "old": repr(doc.get("email_verified")),
            "new": False,
            "reason": "missing/NULL/malformed verification normalized to False",
        })

    # -- Legacy security-question fields --
    for field in LEGACY_FIELDS:
        if field in doc:
            changes.append({
                "field": field,
                "old": repr(doc.get(field))[:80],
                "new": None,
                "reason": "legacy field to be removed",
            })

    # -- Mirror normalized email from _id --
    doc_id = doc.get("_id")
    if isinstance(doc_id, str) and "@" in doc_id:
        normalized = doc_id.strip().lower()
        if normalized and doc.get("email") != normalized:
            changes.append({
                "field": "email",
                "old": repr(doc.get("email")),
                "new": normalized,
                "reason": "mirror normalized email from _id",
            })

    return changes


def normalize_user_records(coll, *, dry_run: bool = False,
                           delete_ids: Set[str] | None = None) -> dict:
    """Idempotent one-pass migration over the users collection.

    When ``dry_run=True`` no records are modified or deleted; the ``changes``
    list in the returned stats dict gives per-record detail so an operator
    can review exactly what would happen.

    Records whose ``_id`` is in ``delete_ids`` (defaults to ``DELETE_IDS``)
    are deleted during apply.  In dry-run mode they appear with
    ``"deleted": True`` but are not removed.

    Returns a stats dict safe to print.  The ``records`` sub-list contains
    one entry per scanned doc with ``_id``, ``changes``, and ``deleted``
    for full auditability.
    """
    _to_delete = delete_ids if delete_ids is not None else DELETE_IDS

    stats: Dict[str, Any] = {
        "skipped": "",
        "scanned": 0,
        "would_modify": 0,
        "actually_modified": 0,
        "would_delete": 0,
        "actually_deleted": 0,
        "verified_kept": 0,
        "unverified_normalized": 0,
        "legacy_fields_removed": 0,
        "email_mirrored": 0,
        "errors": 0,
        "records": [],
    }
    if coll is None:
        stats["skipped"] = "mongo_unavailable"
        return stats

    cursor = coll.find({})

    for doc in cursor:
        try:
            stats["scanned"] += 1
            doc_id = doc.get("_id")
            record_entry: Dict[str, Any] = {"_id": doc_id, "changes": [], "deleted": False}

            # -- Check deletion list --
            if doc_id in _to_delete:
                record_entry["deleted"] = True
                record_entry["changes"] = [{
                    "field": "_id",
                    "old": repr(doc_id),
                    "new": None,
                    "reason": "record deleted per DELETE_IDS (clean start)",
                }]
                stats["would_delete"] += 1

                if not dry_run:
                    coll.delete_one({"_id": doc_id})
                    stats["actually_deleted"] += 1

                stats["records"].append(record_entry)
                continue

            # -- Compute what would change --
            changes = _compute_changes(doc)
            record_entry["changes"] = changes

            if not changes:
                stats["records"].append(record_entry)
                continue

            # -- Count change categories --
            for ch in changes:
                if ch["field"] == "email_verified":
                    if ch["new"] is True:
                        stats["verified_kept"] += 1
                    elif ch["new"] is False:
                        stats["unverified_normalized"] += 1
                elif ch["field"] in LEGACY_FIELDS:
                    stats["legacy_fields_removed"] += 1
                elif ch["field"] == "email":
                    stats["email_mirrored"] += 1

            stats["would_modify"] += 1

            if dry_run:
                stats["records"].append(record_entry)
                continue

            # -- Apply changes --
            set_update: dict = {}
            unset_update: dict = {}

            verdict = _coerce_verified(doc.get("email_verified"))
            if verdict is True and doc.get("email_verified") is not True:
                set_update["email_verified"] = True
            elif verdict is False:
                set_update["email_verified"] = False

            for field in LEGACY_FIELDS:
                if field in doc:
                    unset_update[field] = ""

            if isinstance(doc_id, str) and "@" in doc_id:
                normalized = doc_id.strip().lower()
                if normalized and doc.get("email") != normalized:
                    set_update["email"] = normalized

            update: dict = {}
            if set_update:
                update["$set"] = set_update
            if unset_update:
                update["$unset"] = unset_update
            if update:
                coll.update_one({"_id": doc_id}, update)
                stats["actually_modified"] += 1

            stats["records"].append(record_entry)
        except Exception:
            stats["errors"] += 1

    return stats
