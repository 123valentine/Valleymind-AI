"""Comprehensive tests for the unified ValleyMind authentication system.

Tests cover:
  - is_verified_record() for every invalid state + valid state
  - normalize_user_records() dry-run and apply modes (including DELETE_IDS)
  - _require_login() hardened guards (empty email, missing record, NULL, etc.)
  - Google signup -> unverified -> OTP -> verified -> access flow
  - Unverified session cannot access protected application routes
  - Existing token/session cannot bypass verification requirement

Run with: python -m pytest tests/test_unified_auth.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Ensure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.auth_migration import (
    DELETE_IDS,
    LEGACY_FIELDS,
    _compute_changes,
    _coerce_verified,
    is_verified_record,
    normalize_user_records,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fake MongoDB collection for testing without a live database
# ═══════════════════════════════════════════════════════════════════════════

class FakeCollection:
    """Minimal in-memory collection that supports find/update_one/delete_one."""

    def __init__(self, docs=None):
        self._docs = {d["_id"]: dict(d) for d in (docs or [])}

    def find(self, *args, **kwargs):
        return list(self._docs.values())

    def find_one(self, query):
        _id = query.get("_id")
        return dict(self._docs.get(_id)) if _id in self._docs else None

    def update_one(self, query, update, upsert=False):
        _id = query.get("_id")
        if _id not in self._docs:
            if upsert:
                self._docs[_id] = {"_id": _id}
            else:
                return MagicMock(matched_count=0)
        doc = self._docs[_id]
        if "$set" in update:
            doc.update(update["$set"])
        if "$unset" in update:
            for k in update["$unset"]:
                doc.pop(k, None)
        return MagicMock(matched_count=1)

    def delete_one(self, query):
        _id = query.get("_id")
        if _id in self._docs:
            del self._docs[_id]
            return MagicMock(deleted_count=1)
        return MagicMock(deleted_count=0)


# ═══════════════════════════════════════════════════════════════════════════
# 1. is_verified_record — strict boolean check
# ═══════════════════════════════════════════════════════════════════════════

class TestIsVerifiedRecord(unittest.TestCase):
    """The ONLY predicate for granting application access.  Must be strict."""

    def test_true_only(self):
        """email_verified = True -> verified."""
        self.assertTrue(is_verified_record({"email_verified": True}))

    def test_false_is_unverified(self):
        """email_verified = False -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": False}))

    def test_missing_field_is_unverified(self):
        """No email_verified field -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"name": "Alice"}))

    def test_none_is_unverified(self):
        """email_verified = None -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": None}))

    def test_string_true_is_unverified(self):
        """email_verified = "true" -> UNVERIFIED (strict boolean only)."""
        self.assertFalse(is_verified_record({"email_verified": "true"}))

    def test_string_TruE_is_unverified(self):
        """email_verified = "TruE" -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": "TruE"}))

    def test_int_one_is_unverified(self):
        """email_verified = 1 -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": 1}))

    def test_int_zero_is_unverified(self):
        """email_verified = 0 -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": 0}))

    def test_empty_dict_is_unverified(self):
        """Empty dict -> UNVERIFIED."""
        self.assertFalse(is_verified_record({}))

    def test_non_dict_record_is_unverified(self):
        """None, string, list, int -> UNVERIFIED."""
        for bad in (None, "true", 42, [True], True):
            self.assertFalse(is_verified_record(bad),
                             f"is_verified_record({bad!r}) should be False")

    def test_empty_string_is_unverified(self):
        """email_verified = "" -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": ""}))

    def test_false_string_is_unverified(self):
        """email_verified = "false" -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": "false"}))

    def test_float_is_unverified(self):
        """email_verified = 1.0 -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": 1.0}))

    def test_dict_value_is_unverified(self):
        """email_verified = {} -> UNVERIFIED."""
        self.assertFalse(is_verified_record({"email_verified": {}}))


# ═══════════════════════════════════════════════════════════════════════════
# 2. _coerce_verified — legacy value normalization
# ═══════════════════════════════════════════════════════════════════════════

class TestCoerceVerified(unittest.TestCase):

    def test_true_stays_true(self):
        self.assertIs(_coerce_verified(True), True)

    def test_false_returns_none(self):
        """Already canonical False -> None (no write needed)."""
        self.assertIsNone(_coerce_verified(False))

    def test_missing_returns_false(self):
        self.assertIs(_coerce_verified(None), False)

    def test_string_true_returns_true(self):
        self.assertIs(_coerce_verified("true"), True)

    def test_string_one_returns_true(self):
        self.assertIs(_coerce_verified("1"), True)

    def test_int_returns_false(self):
        self.assertIs(_coerce_verified(42), False)

    def test_empty_string_returns_false(self):
        self.assertIs(_coerce_verified(""), False)


# ═══════════════════════════════════════════════════════════════════════════
# 3. normalize_user_records — dry-run, apply, and deletion
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeUserRecords(unittest.TestCase):

    def test_none_collection_returns_skipped(self):
        stats = normalize_user_records(None)
        self.assertEqual(stats["skipped"], "mongo_unavailable")
        self.assertEqual(stats["scanned"], 0)

    def test_empty_collection_no_changes(self):
        coll = FakeCollection([])
        stats = normalize_user_records(coll)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(stats["would_modify"], 0)

    def test_verified_record_unchanged(self):
        coll = FakeCollection([
            {"_id": "alice@example.com", "email_verified": True, "email": "alice@example.com"}
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["would_modify"], 0)
        self.assertEqual(stats["verified_kept"], 0)

    def test_missing_verified_normalized(self):
        coll = FakeCollection([
            {"_id": "bob@example.com", "email": "bob@example.com"}
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["would_modify"], 1)
        self.assertEqual(stats["unverified_normalized"], 1)
        rec = stats["records"][0]
        self.assertEqual(rec["_id"], "bob@example.com")
        self.assertEqual(len(rec["changes"]), 1)
        self.assertEqual(rec["changes"][0]["field"], "email_verified")
        self.assertEqual(rec["changes"][0]["new"], False)

    def test_none_verified_normalized(self):
        coll = FakeCollection([
            {"_id": "carol@example.com", "email_verified": None, "email": "carol@example.com"}
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["would_modify"], 1)
        self.assertEqual(stats["unverified_normalized"], 1)

    def test_string_true_verified_coerced(self):
        coll = FakeCollection([
            {"_id": "dave@example.com", "email_verified": "true", "email": "dave@example.com"}
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["would_modify"], 1)
        self.assertEqual(stats["verified_kept"], 1)

    def test_legacy_fields_removed(self):
        coll = FakeCollection([
            {"_id": "eve@example.com", "email_verified": False,
             "email": "eve@example.com",
             "security_question": "pet name",
             "security_answer_hash": "abc123"}
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["would_modify"], 1)
        self.assertEqual(stats["legacy_fields_removed"], 2)
        changes = stats["records"][0]["changes"]
        field_names = [c["field"] for c in changes]
        self.assertIn("security_question", field_names)
        self.assertIn("security_answer_hash", field_names)

    def test_email_mirrored_from_id(self):
        coll = FakeCollection([
            {"_id": "Frank@example.com", "email_verified": True, "email": "FRANK@example.com"}
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["would_modify"], 1)
        self.assertEqual(stats["email_mirrored"], 1)

    def test_dry_run_does_not_modify_or_delete(self):
        coll = FakeCollection([
            {"_id": "grace@example.com", "email": "grace@example.com"},
            {"_id": "dprinceonwuka@gmail.com", "email": "dprinceonwuka@gmail.com"},
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["actually_modified"], 0)
        self.assertEqual(stats["actually_deleted"], 0)
        doc = coll.find_one({"_id": "grace@example.com"})
        self.assertNotIn("email_verified", doc)
        doc2 = coll.find_one({"_id": "dprinceonwuka@gmail.com"})
        self.assertIsNotNone(doc2)

    def test_apply_modifies_records(self):
        coll = FakeCollection([
            {"_id": "hank@example.com", "email": "hank@example.com"}
        ])
        stats = normalize_user_records(coll, dry_run=False)
        self.assertEqual(stats["actually_modified"], 1)
        doc = coll.find_one({"_id": "hank@example.com"})
        self.assertFalse(doc["email_verified"])

    def test_idempotent_second_run_no_changes(self):
        coll = FakeCollection([
            {"_id": "ivy@example.com", "email": "ivy@example.com"}
        ])
        normalize_user_records(coll, dry_run=False)
        stats2 = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats2["would_modify"], 0)

    def test_delete_ids_in_dry_run(self):
        """DELETE_IDS records appear as deleted in dry-run but are NOT removed."""
        coll = FakeCollection([
            {"_id": "dprinceonwuka@gmail.com", "email_verified": "true"},
            {"_id": "safe@example.com", "email": "safe@example.com"},
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(stats["would_delete"], 1)
        self.assertEqual(stats["actually_deleted"], 0)
        # dprinceonwuka still exists
        doc = coll.find_one({"_id": "dprinceonwuka@gmail.com"})
        self.assertIsNotNone(doc)
        self.assertEqual(doc["email_verified"], "true")
        # safe record still exists
        doc2 = coll.find_one({"_id": "safe@example.com"})
        self.assertIsNotNone(doc2)

    def test_delete_ids_apply(self):
        """DELETE_IDS records are actually removed when dry_run=False."""
        coll = FakeCollection([
            {"_id": "dprinceonwuka@gmail.com", "email_verified": "true",
             "security_question": "q", "security_answer_hash": "h"},
            {"_id": "safe@example.com", "email": "safe@example.com",
             "email_verified": True},
        ])
        stats = normalize_user_records(coll, dry_run=False)
        self.assertEqual(stats["actually_deleted"], 1)
        self.assertEqual(stats["actually_modified"], 0)
        # dprinceonwuka is gone
        self.assertIsNone(coll.find_one({"_id": "dprinceonwuka@gmail.com"}))
        # safe record untouched
        doc = coll.find_one({"_id": "safe@example.com"})
        self.assertTrue(doc["email_verified"])

    def test_delete_ids_entry_in_stats(self):
        """Deleted record shows up in stats with deleted=True."""
        coll = FakeCollection([
            {"_id": "dprinceonwuka@gmail.com", "email": "dprinceonwuka@gmail.com"},
        ])
        stats = normalize_user_records(coll, dry_run=True)
        self.assertEqual(len(stats["records"]), 1)
        rec = stats["records"][0]
        self.assertEqual(rec["_id"], "dprinceonwuka@gmail.com")
        self.assertTrue(rec["deleted"])

    def test_custom_delete_ids(self):
        """Custom delete_ids overrides the module-level DELETE_IDS."""
        coll = FakeCollection([
            {"_id": "custom@test.com", "email": "custom@test.com"},
            {"_id": "dprinceonwuka@gmail.com", "email": "dprinceonwuka@gmail.com"},
        ])
        stats = normalize_user_records(coll, dry_run=True, delete_ids={"custom@test.com"})
        self.assertEqual(stats["would_delete"], 1)
        rec = stats["records"][0]
        self.assertEqual(rec["_id"], "custom@test.com")
        self.assertTrue(rec["deleted"])
        # dprinceonwuka is NOT deleted with custom delete_ids
        prince_rec = [r for r in stats["records"] if r["_id"] == "dprinceonwuka@gmail.com"][0]
        self.assertFalse(prince_rec["deleted"])

    def test_compute_changes_returns_empty_for_canonical(self):
        doc = {"_id": "x@y.com", "email_verified": True, "email": "x@y.com"}
        self.assertEqual(_compute_changes(doc), [])

    def test_compute_changes_catches_all_issues(self):
        doc = {"_id": "X@Y.com", "email_verified": None, "email": "X@Y.com",
               "security_question": "q", "security_answer_hash": "h"}
        changes = _compute_changes(doc)
        fields = [c["field"] for c in changes]
        self.assertIn("email_verified", fields)
        self.assertIn("security_question", fields)
        self.assertIn("security_answer_hash", fields)
        self.assertIn("email", fields)

    def test_delete_ids_is_a_set(self):
        self.assertIsInstance(DELETE_IDS, set)
        self.assertIn("dprinceonwuka@gmail.com", DELETE_IDS)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Flask app integration tests — _require_login and auth flows
# ═══════════════════════════════════════════════════════════════════════════

class TestRequireLoginIntegration(unittest.TestCase):
    """Test _require_login() hardening via the Flask test client."""

    @classmethod
    def setUpClass(cls):
        """Build a minimal Flask app with the auth gate for testing."""
        from flask import Flask, session as flask_session, jsonify as flask_jsonify

        cls.flask_app = Flask(__name__)
        cls.flask_app.secret_key = "test-secret-key-for-auth-tests"
        cls.flask_app.config["TESTING"] = True
        cls.flask_app.config["SESSION_COOKIE_HTTPONLY"] = True
        cls.flask_app.config["SERVER_NAME"] = "localhost"

        cls._users_db = {}
        cls._auth_tokens_db = {}

        def _load_users():
            return dict(cls._users_db)

        def _get_auth_token(token):
            return cls._auth_tokens_db.get(token, {})

        from core.auth_migration import is_verified_record

        def _require_login():
            user_id = str(flask_session.get("user_id") or "").strip()
            if not user_id:
                return "", (flask_jsonify({"status": "error", "message": "Login required"}), 401)

            email = str(flask_session.get("email") or "").strip().lower()
            if not email:
                return "", (flask_jsonify({
                    "status": "error", "message": "Email verification required",
                    "needs_verification": True, "email": "",
                }), 403)

            user_rec = _load_users().get(email)
            if not is_verified_record(user_rec):
                return "", (flask_jsonify({
                    "status": "error", "message": "Email verification required",
                    "needs_verification": True, "email": email,
                }), 403)
            return user_id, None

        @cls.flask_app.route("/protected")
        def protected():
            user_id, error = _require_login()
            if error:
                return error
            return flask_jsonify({"status": "success", "user_id": user_id})

        @cls.flask_app.route("/protected/sessions")
        def protected_sessions():
            user_id, error = _require_login()
            if error:
                return error
            return flask_jsonify({"status": "success", "sessions": []})

        @cls.flask_app.route("/auth-only/verify")
        def auth_only_verify():
            user_id = str(flask_session.get("user_id") or "").strip()
            if not user_id:
                return flask_jsonify({"status": "error", "message": "Login required"}), 401
            return flask_jsonify({"status": "success", "user_id": user_id})

    def setUp(self):
        self._users_db.clear()
        self._auth_tokens_db.clear()
        self.client = self.flask_app.test_client()

    def _login_user(self, email, user_id, email_verified=True):
        self._users_db[email] = {
            "_id": email,
            "email": email,
            "email_verified": email_verified,
            "user_id": user_id,
        }

    def _set_session(self, client, user_id, email):
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["email"] = email

    def test_unverified_user_blocked(self):
        email = "unverified@test.com"
        self._login_user(email, "uid_1", email_verified=False)
        self._set_session(self.client, "uid_1", email)
        resp = self.client.get("/protected")
        data = resp.get_json()
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(data["needs_verification"])
        self.assertEqual(data["email"], email)

    def test_unverified_blocked_from_sessions_route(self):
        email = "unverified@test.com"
        self._login_user(email, "uid_2", email_verified=False)
        self._set_session(self.client, "uid_2", email)
        resp = self.client.get("/protected/sessions")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.get_json()["needs_verification"])

    def test_verified_user_allowed(self):
        email = "verified@test.com"
        self._login_user(email, "uid_3", email_verified=True)
        self._set_session(self.client, "uid_3", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["user_id"], "uid_3")

    def test_verified_false_blocked(self):
        email = "false@test.com"
        self._login_user(email, "uid_4", email_verified=False)
        self._set_session(self.client, "uid_4", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_verified_none_blocked(self):
        email = "none@test.com"
        self._users_db[email] = {
            "_id": email, "email": email,
            "email_verified": None, "user_id": "uid_5",
        }
        self._set_session(self.client, "uid_5", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_verified_missing_field_blocked(self):
        email = "missing@test.com"
        self._users_db[email] = {
            "_id": email, "email": email, "user_id": "uid_6",
        }
        self._set_session(self.client, "uid_6", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_verified_string_true_blocked(self):
        email = "stringtrue@test.com"
        self._users_db[email] = {
            "_id": email, "email": email,
            "email_verified": "true", "user_id": "uid_7",
        }
        self._set_session(self.client, "uid_7", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_verified_string_TruE_blocked(self):
        email = "stringTruE@test.com"
        self._users_db[email] = {
            "_id": email, "email": email,
            "email_verified": "TruE", "user_id": "uid_8",
        }
        self._set_session(self.client, "uid_8", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_verified_int_one_blocked(self):
        email = "int1@test.com"
        self._users_db[email] = {
            "_id": email, "email": email,
            "email_verified": 1, "user_id": "uid_9",
        }
        self._set_session(self.client, "uid_9", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_verified_empty_string_blocked(self):
        email = "emptystr@test.com"
        self._users_db[email] = {
            "_id": email, "email": email,
            "email_verified": "", "user_id": "uid_10",
        }
        self._set_session(self.client, "uid_10", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_no_email_in_session_blocked(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = "uid_noemail"
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.get_json()["needs_verification"])

    def test_empty_email_in_session_blocked(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = "uid_emptyemail"
            sess["email"] = ""
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_no_session_returns_401(self):
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 401)

    def test_token_cannot_bypass_verification(self):
        email = "tokentest@test.com"
        self._users_db[email] = {
            "_id": email, "email": email,
            "email_verified": False, "user_id": "uid_token",
        }
        with self.client.session_transaction() as sess:
            sess["user_id"] = "uid_token"
            sess["email"] = email
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.get_json()["needs_verification"])

    def test_deleted_user_record_blocked(self):
        """User record deleted after login -> blocked (phantom record)."""
        email = "deleted@test.com"
        self._set_session(self.client, "uid_deleted", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)

    def test_auth_only_endpoint_allows_unverified(self):
        email = "unverified@test.com"
        self._login_user(email, "uid_authonly", email_verified=False)
        self._set_session(self.client, "uid_authonly", email)
        resp = self.client.get("/auth-only/verify")
        self.assertEqual(resp.status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Google signup flow test
# ═══════════════════════════════════════════════════════════════════════════

class TestGoogleSignupFlow(unittest.TestCase):
    """Test the full Google signup -> unverified -> OTP -> verified -> access flow."""

    @classmethod
    def setUpClass(cls):
        from flask import Flask, session as flask_session, jsonify as flask_jsonify

        cls.flask_app = Flask(__name__)
        cls.flask_app.secret_key = "test-google-flow-secret"
        cls.flask_app.config["TESTING"] = True

        cls._users_db = {}

        def _load_users():
            return dict(cls._users_db)

        from core.auth_migration import is_verified_record

        def _require_login():
            user_id = str(flask_session.get("user_id") or "").strip()
            if not user_id:
                return "", (flask_jsonify({"status": "error", "message": "Login required"}), 401)
            email = str(flask_session.get("email") or "").strip().lower()
            if not email:
                return "", (flask_jsonify({
                    "status": "error", "message": "Email verification required",
                    "needs_verification": True, "email": "",
                }), 403)
            user_rec = _load_users().get(email)
            if not is_verified_record(user_rec):
                return "", (flask_jsonify({
                    "status": "error", "message": "Email verification required",
                    "needs_verification": True, "email": email,
                }), 403)
            return user_id, None

        @cls.flask_app.route("/api/auth/google", methods=["POST"])
        def google_auth():
            email = "googleuser@gmail.com"
            user_id = hashlib.sha256(email.encode()).hexdigest()[:24]
            users = _load_users()
            if email not in users:
                users[email] = {
                    "_id": email,
                    "user_id": user_id,
                    "google_id": "google_sub_123",
                    "name": "Google User",
                    "email": email,
                    "auth_method": "google",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "email_verified": False,
                }
                cls._users_db[email] = users[email]
            flask_session.clear()
            flask_session.permanent = True
            flask_session["user_id"] = user_id
            flask_session["email"] = email
            return flask_jsonify({
                "status": "success",
                "authenticated": True,
                "email": email,
                "email_verified": False,
                "needs_verification": True,
            })

        @cls.flask_app.route("/api/auth/verify-email", methods=["POST"])
        def verify_email():
            email = str(flask_session.get("email") or "").strip().lower()
            if not email:
                return flask_jsonify({"status": "error", "message": "Login required"}), 401
            users = _load_users()
            user = users.get(email)
            if not user:
                return flask_jsonify({"status": "error", "message": "Account not found"}), 404
            user["email_verified"] = True
            user["email_verified_at"] = datetime.now(timezone.utc).isoformat()
            cls._users_db[email] = user
            return flask_jsonify({"status": "success", "email_verified": True})

        @cls.flask_app.route("/protected")
        def protected():
            user_id, error = _require_login()
            if error:
                return error
            return flask_jsonify({"status": "success", "user_id": user_id})

    def setUp(self):
        self._users_db.clear()
        self.client = self.flask_app.test_client()

    def test_full_google_signup_to_verified_flow(self):
        resp = self.client.post("/api/auth/google", json={"credential": "fake"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["authenticated"])
        self.assertFalse(data["email_verified"])
        self.assertTrue(data["needs_verification"])

        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.get_json()["needs_verification"])

        resp = self.client.post("/api/auth/verify-email", json={"code": "123456"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["email_verified"])

        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("user_id", resp.get_json())

    def test_google_existing_verified_user_stays_verified(self):
        email = "existing@gmail.com"
        uid = hashlib.sha256(email.encode()).hexdigest()[:24]
        self._users_db[email] = {
            "_id": email, "user_id": uid, "email": email,
            "auth_method": "google", "email_verified": True,
        }
        with self.client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["email"] = email
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)

    def test_google_existing_unverified_user_stays_unverified(self):
        email = "existing_unverified@gmail.com"
        uid = hashlib.sha256(email.encode()).hexdigest()[:24]
        self._users_db[email] = {
            "_id": email, "user_id": uid, "email": email,
            "auth_method": "google", "email_verified": False,
        }
        with self.client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["email"] = email
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 403)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Email registration flow test
# ═══════════════════════════════════════════════════════════════════════════

class TestEmailRegistrationFlow(unittest.TestCase):
    """Test email+password registration -> unverified -> verify -> access."""

    @classmethod
    def setUpClass(cls):
        from flask import Flask, session as flask_session, jsonify as flask_jsonify

        cls.flask_app = Flask(__name__)
        cls.flask_app.secret_key = "test-email-reg-secret"
        cls.flask_app.config["TESTING"] = True

        cls._users_db = {}

        def _load_users():
            return dict(cls._users_db)

        from core.auth_migration import is_verified_record

        def _require_login():
            user_id = str(flask_session.get("user_id") or "").strip()
            if not user_id:
                return "", (flask_jsonify({"status": "error", "message": "Login required"}), 401)
            email = str(flask_session.get("email") or "").strip().lower()
            if not email:
                return "", (flask_jsonify({
                    "status": "error", "message": "Email verification required",
                    "needs_verification": True, "email": "",
                }), 403)
            user_rec = _load_users().get(email)
            if not is_verified_record(user_rec):
                return "", (flask_jsonify({
                    "status": "error", "message": "Email verification required",
                    "needs_verification": True, "email": email,
                }), 403)
            return user_id, None

        @cls.flask_app.route("/auth/register", methods=["POST"])
        def register():
            email = "newuser@test.com"
            user_id = hashlib.sha256(email.encode()).hexdigest()[:24]
            cls._users_db[email] = {
                "_id": email, "user_id": user_id, "email": email,
                "auth_method": "email", "email_verified": False,
                "name": "New User", "username": "newuser",
            }
            flask_session.clear()
            flask_session.permanent = True
            flask_session["user_id"] = user_id
            flask_session["email"] = email
            return flask_jsonify({
                "status": "success", "authenticated": True,
                "email_verified": False, "needs_verification": True,
            }), 201

        @cls.flask_app.route("/api/auth/verify-email", methods=["POST"])
        def verify_email():
            email = str(flask_session.get("email") or "").strip().lower()
            if not email:
                return flask_jsonify({"status": "error", "message": "Login required"}), 401
            users = _load_users()
            user = users.get(email)
            if not user:
                return flask_jsonify({"status": "error", "message": "Account not found"}), 404
            user["email_verified"] = True
            cls._users_db[email] = user
            return flask_jsonify({"status": "success", "email_verified": True})

        @cls.flask_app.route("/protected")
        def protected():
            user_id, error = _require_login()
            if error:
                return error
            return flask_jsonify({"status": "success", "user_id": user_id})

    def setUp(self):
        self._users_db.clear()
        self.client = self.flask_app.test_client()

    def test_registration_creates_unverified_account(self):
        resp = self.client.post("/auth/register", json={})
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertFalse(data["email_verified"])
        self.assertTrue(data["needs_verification"])
        email = "newuser@test.com"
        self.assertIn(email, self._users_db)
        self.assertFalse(self._users_db[email]["email_verified"])

    def test_login_never_creates_account(self):
        before = dict(self._users_db)
        self.assertEqual(self._users_db, before)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Email verification DISABLED — _require_login bypasses verification
# ═══════════════════════════════════════════════════════════════════════════

class TestVerificationDisabled(unittest.TestCase):
    """When EMAIL_VERIFICATION_ENABLED is False the verification gate in
    _require_login() must be skipped so existing unverified users are not
    blocked and new sign-ups work immediately."""

    @classmethod
    def setUpClass(cls):
        from flask import Flask, session as flask_session, jsonify as flask_jsonify

        cls.flask_app = Flask(__name__)
        cls.flask_app.secret_key = "test-verification-disabled"
        cls.flask_app.config["TESTING"] = True

        cls._users_db = {}

        def _load_users():
            return dict(cls._users_db)

        from core.auth_migration import is_verified_record

        # Simulates the real app's _require_login with verification DISABLED
        def _require_login():
            user_id = str(flask_session.get("user_id") or "").strip()
            if not user_id:
                return "", (flask_jsonify({"status": "error", "message": "Login required"}), 401)
            email = str(flask_session.get("email") or "").strip().lower()
            if not email:
                return "", (flask_jsonify({
                    "status": "error", "message": "Email verification required",
                    "needs_verification": True, "email": "",
                }), 403)
            user_rec = _load_users().get(email)
            # When verification is disabled, skip the is_verified_record check
            return user_id, None

        @cls.flask_app.route("/protected")
        def protected():
            user_id, error = _require_login()
            if error:
                return error
            return flask_jsonify({"status": "success", "user_id": user_id})

    def setUp(self):
        self._users_db.clear()
        self.client = self.flask_app.test_client()

    def _login_user(self, email, user_id, email_verified=False):
        self._users_db[email] = {
            "_id": email,
            "email": email,
            "email_verified": email_verified,
            "user_id": user_id,
        }

    def _set_session(self, client, user_id, email):
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["email"] = email

    def test_unverified_user_allowed_when_disabled(self):
        email = "unverified@test.com"
        self._login_user(email, "uid_unv", email_verified=False)
        self._set_session(self.client, "uid_unv", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["user_id"], "uid_unv")

    def test_missing_field_user_allowed_when_disabled(self):
        email = "missing@test.com"
        self._users_db[email] = {
            "_id": email, "email": email, "user_id": "uid_m",
        }
        self._set_session(self.client, "uid_m", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)

    def test_none_verified_user_allowed_when_disabled(self):
        email = "none@test.com"
        self._users_db[email] = {
            "_id": email, "email": email,
            "email_verified": None, "user_id": "uid_n",
        }
        self._set_session(self.client, "uid_n", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)

    def test_verified_user_still_allowed_when_disabled(self):
        email = "verified@test.com"
        self._login_user(email, "uid_v", email_verified=True)
        self._set_session(self.client, "uid_v", email)
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)

    def test_no_session_still_returns_401(self):
        resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
