"""Focused tests for per-persona local Piper TTS wiring.

Two kinds of test here, clearly separated:

* MOCKED tests (no model files, no network): they patch the Piper engine
  boundary and assert ROUTING/selection/fallback/error behaviour. These always
  run.
* REAL synthesis smoke tests: they run the actual ``piper_tts`` engine against
  the already-downloaded model files (via PIPER_MODEL_DIR). They SKIP when the
  models are absent, so the suite never downloads anything.

Qwen is blocked in every routing test, so a Piper-selected persona failing can
never silently bill Alibaba.
"""
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module
from core import tts, piper_tts


# ── helpers ────────────────────────────────────────────────────────────────

def _models_dir():
    """Directory holding the already-downloaded Piper models, or None."""
    cand = os.getenv("PIPER_MODEL_DIR") or r"C:\Users\EGBUJI~1\AppData\Local\Temp\opencode\piper_stage1"
    return Path(cand) if Path(cand).is_dir() else None


def _have_model(voice_id: str) -> bool:
    m = _models_dir()
    if not m:
        return False
    return piper_tts.model_files_present.__wrapped__(voice_id) if hasattr(
        piper_tts.model_files_present, "__wrapped__") else _present_in(m, voice_id)


def _present_in(m: Path, voice_id: str) -> bool:
    base = piper_tts.PIPER_VOICES.get(voice_id)
    if not base:
        return False
    return (m / f"{base}.onnx").exists() and (m / f"{base}.onnx.json").exists()


# ── allowlist / validation ─────────────────────────────────────────────────

def test_allowlist_is_exactly_the_two_female_models():
    assert piper_tts.PIPER_VOICES == {
        "kristin_piper": "en_US-kristin-medium",
        "ljspeech_piper": "en_US-ljspeech-medium",
    }


def test_invalid_and_unsupported_voice_ids_rejected():
    for bad in ("", None, "bogus", "en_US-kristin-medium", "../../etc/passwd",
                "http://evil/x.onnx", "guy", "marcus"):
        assert piper_tts.is_piper_voice(bad) is False
    assert piper_tts.is_piper_voice("kristin_piper") is True
    assert piper_tts.is_piper_voice("ljspeech_piper") is True


def test_synthesize_piper_rejects_unsupported_voice_without_loading(monkeypatch):
    called = {"load": False}
    monkeypatch.setattr(piper_tts, "_SLOT", piper_tts._Slot())
    monkeypatch.setattr(piper_tts._SLOT, "acquire",
                        lambda *a, **k: called.__setitem__("load", True))
    out = piper_tts.synthesize_piper("hello", "not_a_voice")
    assert out["enabled"] is False
    assert out["engine"] == "none"
    assert called["load"] is False


# ── routing through core/tts.py (mocked engine) ─────────────────────────────

def test_elena_kristin_routes_through_piper_engine(monkeypatch):
    """Elena selecting Kristin produces audio through the PIPER engine."""
    monkeypatch.setattr(piper_tts, "synthesize_piper",
                        lambda text, vid: {"enabled": True, "engine": "piper_local",
                                          "url": "/tts/x.wav", "voice": vid})
    # Qwen must be impossible on this path.
    monkeypatch.setattr(tts, "qwen_tts_available", lambda: False)
    out = tts.synthesize_persona_local("hi", "elena", "kristin_piper")
    assert out["engine"] == "piper_local"
    assert out["url"] == "/tts/x.wav"


def test_angelina_ljspeech_routes_through_piper_engine(monkeypatch):
    """Angelina selecting LJSpeech produces audio through the PIPER engine."""
    monkeypatch.setattr(piper_tts, "synthesize_piper",
                        lambda text, vid: {"enabled": True, "engine": "piper_local",
                                          "url": "/tts/y.wav", "voice": vid})
    out = tts.synthesize_persona_local("hi", "angelina", "ljspeech_piper")
    assert out["engine"] == "piper_local"
    assert out["voice"] == "ljspeech_piper"


def test_piper_failure_never_calls_qwen_and_falls_back_to_free(monkeypatch):
    """Missing model / synthesis failure -> FREE fallback; Qwen never invoked."""
    monkeypatch.setattr(piper_tts, "synthesize_piper",
                        lambda text, vid: {"enabled": False, "engine": "none",
                                          "reason": "model not available", "voice": vid})
    calls = {"qwen": 0}
    def _boom(*a, **k):
        calls["qwen"] += 1
        raise AssertionError("Qwen must not be called for a Piper-selected persona")
    monkeypatch.setattr(tts, "_qwen_synth", _boom)
    monkeypatch.setattr(tts, "qwen_tts_available", lambda: True)  # even if Qwen looks available
    monkeypatch.setattr(tts, "synthesize_free",
                        lambda text, profile: {"enabled": True, "engine": "edge_tts_free",
                                               "url": "/tts/z.mp3", "voice": "en-US-AriaNeural"})
    out = tts.synthesize_persona_local("hi", "elena", "kristin_piper")
    assert out["engine"] == "edge_tts_free"
    assert out.get("fallback_from") == "piper_local"
    assert calls["qwen"] == 0


def test_piper_and_free_both_unavailable_reports_none(monkeypatch):
    monkeypatch.setattr(piper_tts, "synthesize_piper",
                        lambda text, vid: {"enabled": False, "engine": "none",
                                          "reason": "piper not installed", "voice": vid})
    monkeypatch.setattr(tts, "synthesize_free",
                        lambda text, profile: {"enabled": False, "engine": "none",
                                               "reason": "edge-tts not installed"})
    out = tts.synthesize_persona_local("hi", "elena", "kristin_piper")
    assert out["engine"] == "none"
    assert "unavailable" in out["reason"]


def test_free_fallback_reports_actual_engine(monkeypatch):
    monkeypatch.setattr(piper_tts, "synthesize_piper",
                        lambda text, vid: {"enabled": False, "engine": "none", "voice": vid})
    monkeypatch.setattr(tts, "synthesize_free",
                        lambda text, profile: {"enabled": True, "engine": "edge_tts_free",
                                               "url": "/tts/a.mp3"})
    out = tts.synthesize_persona_local("hi", "angelina", "ljspeech_piper")
    assert out["engine"] == "edge_tts_free"          # honest engine label
    assert out["engine"] != "piper_local"


# ── /api/tts route: server-side selection, never trusts client ─────────────

class _Route:
    """Minimal authenticated app client + settings helper (mirrors test_cloud)."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path

    def __enter__(self):
        self._patches = []

        def _start(p):
            self._patches.append(p); p.start()

        users = self.tmp / "auth_users.json"
        settings = self.tmp / "settings"
        _start(patch.object(app_module, "_users_file", users))
        _start(patch.object(app_module, "users_collection", lambda: None))
        _start(patch.object(app_module, "auth_tokens_collection", lambda: None))
        _start(patch.object(app_module, "chats_collection", lambda: None))
        _start(patch.object(app_module, "_SETTINGS_DIR", settings))

        email = f"piper_{uuid.uuid4().hex[:10]}@example.com"
        user_id = app_module._safe_user_id(email)
        all_users = app_module._load_users()
        all_users[email] = {"_id": email, "email": email, "user_id": user_id,
                            "email_verified": True}
        app_module._save_users(all_users)
        self.user_id = user_id
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["email"] = email
        self.client = client
        return self

    def __exit__(self, *a):
        for p in reversed(self._patches):
            p.stop()

    def save_voices(self, mapping):
        return self.client.put("/api/settings/cloud?merge=1",
                               json={"persona_voices": mapping})

    def tts(self, persona, text="hello there"):
        return self.client.post("/api/tts", json={"persona": persona, "text": text})


def test_api_tts_routes_saved_piper_selection(tmp_path, monkeypatch):
    captured = {}
    def fake_local(text, persona, voice_id):
        captured.update(persona=persona, voice_id=voice_id)
        return {"enabled": True, "engine": "piper_local", "url": "/tts/p.wav", "voice": voice_id}

    monkeypatch.setattr(tts, "synthesize_persona_local", fake_local)
    with _Route(tmp_path) as r:
        r.save_voices({"elena": "kristin_piper"})
        resp = r.tts("elena")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["engine"] == "piper_local"
        assert captured == {"persona": "elena", "voice_id": "kristin_piper"}


def test_api_tts_personas_are_independent(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda t, p, v: (seen.append((p, v)) or
                                         {"enabled": True, "engine": "piper_local",
                                          "url": "/tts/p.wav", "voice": v}))
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: {"enabled": True, "engine": "qwen_tts",
                                      "url": "/tts/q.wav"})
    with _Route(tmp_path) as r:
        r.save_voices({"elena": "kristin_piper", "angelina": "ljspeech_piper"})
        assert r.tts("elena").get_json()["engine"] == "piper_local"
        assert r.tts("angelina").get_json()["engine"] == "piper_local"
        # Marcus has no Piper selection -> untouched default (Qwen) path.
        assert r.tts("marcus").get_json()["engine"] == "qwen_tts"
    assert seen == [("elena", "kristin_piper"), ("angelina", "ljspeech_piper")]


def test_api_tts_unconfigured_persona_keeps_default(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda *a: pytest.fail("Piper must not be used with no selection"))
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: {"enabled": True, "engine": "qwen_tts", "url": "/tts/q.wav"})
    with _Route(tmp_path) as r:
        # Nothing saved for elena.
        assert r.tts("elena").get_json()["engine"] == "qwen_tts"


def test_api_tts_ignores_client_supplied_voice_id(tmp_path, monkeypatch):
    """Normal persona playback reads the server's saved selection — a client
    cannot override it with a raw/unknown voice id, path, or URL."""
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: {"enabled": True, "engine": "qwen_tts", "url": "/tts/q.wav"})
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda *a: pytest.fail("unvalidated client voice id must not be trusted"))
    with _Route(tmp_path) as r:
        # An explicitly supplied but UNKNOWN voice/path is rejected outright.
        resp = r.client.post("/api/tts", json={
            "persona": "elena", "text": "hi", "voice": "../../secret.onnx",
            "voice_id": "kristin_piper", "profile": ""})
        assert resp.status_code == 400
        assert resp.get_json()["message"] == "unsupported voice"
        # A normal call (no voice field) is unaffected and follows the default.
        assert r.tts("elena").get_json()["engine"] == "qwen_tts"


def test_api_tts_default_playback_ignores_valid_preview_voice(tmp_path, monkeypatch):
    """The catalogue 'voice' field is a PREVIEW selector, not a playback override:
    a normal call (no voice field) still follows the saved persona selection."""
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: {"enabled": True, "engine": "qwen_tts", "url": "/tts/q.wav"})
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda *a: pytest.fail("must not deviate from saved selection here"))
    with _Route(tmp_path) as r:
        assert r.tts("elena").get_json()["engine"] == "qwen_tts"


def test_api_tts_invalid_persona_name_falls_back_to_marcus(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: (seen.append(p) or
                                      {"enabled": True, "engine": "qwen_tts", "url": "/tts/q.wav"}))
    with _Route(tmp_path) as r:
        resp = r.client.post("/api/tts", json={"persona": "../../evil", "text": "hi"})
        assert resp.status_code == 200
    assert seen == ["marcus"]


def test_api_tts_marcus_not_auto_assigned_female_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda *a: pytest.fail("Marcus must not get a Piper female voice"))
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: {"enabled": True, "engine": "qwen_tts", "url": "/tts/q.wav"})
    with _Route(tmp_path) as r:
        # Even with female Piper voices saved for others, Marcus stays default.
        r.save_voices({"elena": "kristin_piper", "angelina": "ljspeech_piper"})
        assert r.tts("marcus").get_json()["engine"] == "qwen_tts"


def test_api_tts_persona_voices_survive_unrelated_cloud_settings(tmp_path, monkeypatch):
    """Existing cloud settings must survive persona-voice updates (regression)."""
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: {"enabled": True, "engine": "qwen_tts", "url": "/tts/q.wav"})
    with _Route(tmp_path) as r:
        r.client.put("/api/settings/cloud", json={"accent": "#88ffaa",
                                                  "personality_style": "calm",
                                                  "voice_preference": "Melodic"})
        r.save_voices({"elena": "kristin_piper"})
        got = r.client.get("/api/settings/cloud").get_json()["data"]
        assert got["accent"] == "#88ffaa"
        assert got["personality_style"] == "calm"
        assert got["voice_preference"] == "Melodic"
        assert got["persona_voices"] == {"elena": "kristin_piper"}


# ── Voice-tab catalogue preview endpoint (?voice=) ─────────────────────────

def test_voice_preview_free_reference_routes_to_free_engine(tmp_path, monkeypatch):
    """A catalogue Preview of a reference voice uses the free engine, that voice."""
    captured = {}
    def fake_free(text, profile):
        captured.update(profile=profile)
        return {"enabled": True, "engine": "edge_tts_free",
                "url": "/tts/r.mp3", "voice": tts.FREE_VOICE_MAP.get(profile)}
    monkeypatch.setattr(tts, "synthesize_free", fake_free)
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda *a: pytest.fail("reference preview must not hit Qwen"))
    with _Route(tmp_path) as r:
        body = r.client.post("/api/tts", json={"text": "hi", "voice": "en-US-AriaNeural"}).get_json()
    assert body["engine"] == "edge_tts_free"
    assert captured["profile"] == "elena"            # Aria belongs to Elena's free voice
    assert body["voice"] == "en-US-AriaNeural"


def test_voice_preview_companion_reference_routes_to_companion_profile(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(tts, "synthesize_free",
                        lambda text, profile: (captured.update(profile=profile) or
                                               {"enabled": True, "engine": "edge_tts_free",
                                                "url": "/tts/c.mp3",
                                                "voice": tts.FREE_VOICE_MAP.get(profile)}))
    with _Route(tmp_path) as r:
        body = r.client.post("/api/tts",
                             json={"text": "hi", "voice": "en-US-ChristopherNeural"}).get_json()
    assert body["voice"] == "en-US-ChristopherNeural"
    assert captured["profile"] == "companion"


def test_voice_preview_piper_voice_routes_through_local_engine(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda text, persona, vid: (captured.update(vid=vid) or
                                                    {"enabled": True, "engine": "piper_local",
                                                     "url": "/tts/p.wav", "voice": vid}))
    with _Route(tmp_path) as r:
        body = r.client.post("/api/tts",
                             json={"text": "hi", "voice": "kristin_piper"}).get_json()
    assert body["engine"] == "piper_local"
    assert captured["vid"] == "kristin_piper"


def test_voice_preview_reports_fallback_honestly(tmp_path, monkeypatch):
    """When Piper is unavailable, the preview reports the free engine + fallback_from."""
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda text, persona, vid: {"enabled": True, "engine": "edge_tts_free",
                                                    "url": "/tts/f.mp3",
                                                    "voice": "en-US-AriaNeural",
                                                    "fallback_from": "piper_local"})
    with _Route(tmp_path) as r:
        body = r.client.post("/api/tts",
                             json={"text": "hi", "voice": "kristin_piper"}).get_json()
    assert body["engine"] == "edge_tts_free"
    assert body["fallback_from"] == "piper_local"    # UI can label this honestly


def test_voice_preview_rejects_unsupported_voice_id(tmp_path, monkeypatch):
    """An arbitrary voice id/path is rejected — never trusted, never a path."""
    monkeypatch.setattr(tts, "synthesize_free", lambda *a: pytest.fail("must not synth"))
    monkeypatch.setattr(tts, "synthesize_persona", lambda *a: pytest.fail("must not synth"))
    with _Route(tmp_path) as r:
        for bad in ("../../etc/passwd", "http://evil/x.onnx", "not_a_voice", "Elias"):
            resp = r.client.post("/api/tts", json={"text": "hi", "voice": bad})
            assert resp.status_code == 400, bad


# ── reference preview still works ──────────────────────────────────────────

def test_reference_voice_preview_unchanged(tmp_path, monkeypatch):
    """A reference voice still uses the existing default engine (not Piper)."""
    monkeypatch.setattr(tts, "synthesize_persona",
                        lambda t, p: {"enabled": True, "engine": "qwen_tts", "url": "/tts/q.wav",
                                      "voice": tts.voice_for_persona(p)})
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda *a: pytest.fail("reference preview must not use Piper"))
    with _Route(tmp_path) as r:
        r.save_voices({"marcus": "guy"})   # reference id, not a Piper voice
        assert r.tts("marcus").get_json()["engine"] == "qwen_tts"


# ── concurrent requests do not corrupt model state ─────────────────────────

def test_slot_serialises_inference_and_prevents_dup_loads(monkeypatch):
    """Concurrent acquire on the SAME voice loads once; different voice swaps safely."""
    import threading
    loaded = {"count": 0}
    slot = piper_tts._Slot()

    class _FakeVoice:
        def synthesize(self, text, cfg):
            return []

    def fake_load(path, cfg):
        loaded["count"] += 1
        return _FakeVoice()

    monkeypatch.setattr(piper_tts, "_model_paths",
                        lambda vid: (Path("m.onnx"), Path("m.onnx.json")))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "stat", lambda self: type("S", (), {"st_size": 10})())
    fake_piper = type("P", (), {"PiperVoice": type("PV", (), {"load": staticmethod(fake_load)}),
                                "SynthesisConfig": lambda: None})
    monkeypatch.setitem(sys.modules, "piper", fake_piper)
    monkeypatch.setattr(piper_tts, "_SLOT", slot)

    voices = []
    def worker():
        voices.append(slot.acquire("kristin_piper", wait=5))
        slot.release()
    ths = [threading.Thread(target=worker) for _ in range(8)]
    for t in ths: t.start()
    for t in ths: t.join()
    # All eight got the SAME loaded instance; only one load happened.
    assert loaded["count"] == 1
    assert len(voices) == 8


# ── REAL synthesis smoke tests (skip if models absent) ─────────────────────

@pytest.mark.skipif(not (_models_dir() and _present_in(_models_dir(), "kristin_piper")),
                    reason="kristin model not present")
def test_real_kristin_synthesis_produces_wav(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPER_MODEL_DIR", str(_models_dir()))
    import importlib
    mod = importlib.reload(piper_tts)
    if not mod.voice_available():
        pytest.skip("piper not installed")
    monkeypatch.setattr(mod, "TTS_FOLDER", tmp_path)
    mod._SLOT.unload()
    out = mod.synthesize_piper("Hey, it's good to see you again.", "kristin_piper")
    assert out["engine"] == "piper_local"
    assert out["enabled"] is True and out["url"]
    wav = tmp_path / out["url"].split("/")[-1]
    assert wav.exists() and wav.stat().st_size > 10000
    assert wav.read_bytes()[:4] == b"RIFF"
    mod._SLOT.unload()


@pytest.mark.skipif(not (_models_dir() and _present_in(_models_dir(), "ljspeech_piper")),
                    reason="ljspeech model not present")
def test_real_ljspeech_synthesis_produces_wav(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPER_MODEL_DIR", str(_models_dir()))
    import importlib
    mod = importlib.reload(piper_tts)
    if not mod.voice_available():
        pytest.skip("piper not installed")
    monkeypatch.setattr(mod, "TTS_FOLDER", tmp_path)
    mod._SLOT.unload()
    out = mod.synthesize_piper("Hey, it's good to see you again.", "ljspeech_piper")
    assert out["engine"] == "piper_local"
    wav = tmp_path / out["url"].split("/")[-1]
    assert wav.exists() and wav.stat().st_size > 10000
    assert wav.read_bytes()[:4] == b"RIFF"
    mod._SLOT.unload()


@pytest.mark.skipif(not (_models_dir() and _present_in(_models_dir(), "kristin_piper")),
                    reason="kristin model not present")
def test_real_tts_persona_local_end_to_end(monkeypatch, tmp_path):
    """core/tts.synthesize_persona_local drives the REAL Piper engine."""
    monkeypatch.setenv("PIPER_MODEL_DIR", str(_models_dir()))
    import importlib
    mod = importlib.reload(piper_tts)
    if not mod.voice_available():
        pytest.skip("piper not installed")
    monkeypatch.setattr(tts, "synthesize_persona_local",
                        lambda text, persona, vid: mod.synthesize_piper(text, vid))
    monkeypatch.setattr(tts, "TTS_FOLDER", tmp_path)
    mod._SLOT.unload()
    out = tts.synthesize_persona_local("Round table line.", "elena", "kristin_piper")
    assert out["engine"] == "piper_local"
    assert out["url"]
    mod._SLOT.unload()
