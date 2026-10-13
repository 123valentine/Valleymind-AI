"""Music workstation HTTP API.

Registered onto the main Flask app by ``core.music.register_routes`` so every
endpoint shares ValleyMind's exact authentication/verification gate. The
frontend talks only to this backend; which external provider actually renders
audio is decided here, never hard-coded in the UI.

Heavy operations (generation, remix, extend, reference analysis, stems, vocal
processing, mastering, track generation) run as async jobs and return a job id
(see ``core.music.jobs``). Creative work — which is real LLM output — completes
in the job and carries no fake audio. Audio rendering is attempted through the
active provider and reported honestly when unavailable.

Routes are intentionally namespaced so they never collide with the older
``/api/music`` production endpoints already registered in ``app.py``.
"""

from __future__ import annotations

import json
import time
import uuid

from flask import jsonify, request, send_file

from core.music import african, audio, engine, jobs, ops, projects, providers, storage
from core.music.providers import MusicError, ProviderUnavailable

# Feature registry surfaced to the UI so it can disable what a provider cannot
# do — the interface stays, the promise stays honest.
MUSIC_FEATURES = [
    {"id": "create", "label": "AI Create", "group": "Create",
     "server_audio": True, "creative_only": True},
    {"id": "track_generate", "label": "AI Track Generation", "group": "Create",
     "server_audio": True, "creative_only": True},
    {"id": "reference", "label": "Reference / Inspire", "group": "Create",
     "server_audio": False, "creative_only": True},
    {"id": "remix", "label": "AI Remix", "group": "Transform",
     "server_audio": True, "creative_only": True},
    {"id": "extend", "label": "Continue / Extend", "group": "Transform",
     "server_audio": True, "creative_only": True},
    {"id": "vocal_lab", "label": "Vocal Lab", "group": "Vocals",
     "server_audio": True, "creative_only": True},
    {"id": "stems", "label": "Stem Separation", "group": "Transform",
     "server_audio": True, "creative_only": False},
    {"id": "master", "label": "AI Master", "group": "Finish",
     "server_audio": True, "creative_only": False},
    {"id": "audio_tools", "label": "Audio Tools", "group": "Process",
     "server_audio": True, "creative_only": False},
    {"id": "lyrics", "label": "Lyrics Lab", "group": "Write",
     "server_audio": False, "creative_only": True},
    {"id": "copilot", "label": "Elena Music Co-Pilot", "group": "Assist",
     "server_audio": False, "creative_only": True},
]

REMIX_PRESETS = [
    "Change genre", "Change mood", "Change tempo", "Change instrumentation",
    "Make darker", "Make happier", "Make more energetic", "Make more emotional",
    "Afrobeat version", "Afropop version", "Amapiano version", "R&B version",
    "Highlife version", "Gospel version", "Cinematic version",
    "Acoustic version", "Club version",
]

MASTER_TARGETS = [
    {"id": "streaming", "label": "Streaming", "target_lufs": -14.0},
    {"id": "youtube", "label": "YouTube", "target_lufs": -14.0},
    {"id": "tiktok", "label": "TikTok", "target_lufs": -13.0},
    {"id": "general", "label": "General", "target_lufs": -14.0},
    {"id": "loud", "label": "Loud", "target_lufs": -9.0},
    {"id": "warm", "label": "Warm", "target_lufs": -12.0},
    {"id": "clean", "label": "Clean", "target_lufs": -14.0},
    {"id": "cinematic", "label": "Cinematic", "target_lufs": -18.0},
]


def init_routes(app, require_login, *, safe_user_id=None, projects_dir=None):
    """Register every workstation route. ``require_login`` is app._require_login."""
    projects.configure(safe_user_id=safe_user_id, projects_dir=projects_dir)

    def _auth():
        user_id, error = require_login()
        if error:
            return None, error
        return user_id, None

    def _err(message, status=400, code="bad_request", **extra):
        payload = {"status": "error", "message": message, "error_code": code}
        payload.update(extra)
        return jsonify(payload), status

    def _body():
        return request.get_json(silent=True) or {}

    def _try_provider(method: str, ctx: dict) -> dict:
        """Attempt a provider audio op, returning an honest descriptor.

        Never raises; an unavailable provider becomes a structured
        ``{rendered: False, unavailable: True, ...}`` so creatives still succeed.
        """
        provider = providers.get_provider()
        fn = getattr(provider, method, None)
        if fn is None:
            return {"rendered": False, "unavailable": True, "capability": method,
                    "note": f"{provider.provider_label} does not support this operation."}
        try:
            result = fn(ctx) or {}
            if isinstance(result, dict):
                result.setdefault("rendered", False)
                result.setdefault("provider", provider.provider_id)
                return result
            return {"rendered": False, "provider": provider.provider_id}
        except ProviderUnavailable as exc:
            return {"rendered": False, "unavailable": True,
                    "capability": getattr(exc, "capability", method),
                    "note": str(exc)}
        except MusicError as exc:
            return {"rendered": False, "error": str(exc),
                    "error_code": getattr(exc, "code", "music_error")}
        except Exception as exc:  # noqa: BLE001
            return {"rendered": False, "error": f"Provider error: {exc}",
                    "error_code": "provider_error"}

    def _source_package_from(body: dict) -> dict:
        """Resolve the audio/creative source for remix/extend/master."""
        project_id = str(body.get("project_id") or "").strip()
        if project_id:
            user_id = body.get("_user_id")
            project = projects.get_project(user_id, project_id) if user_id else None
            if project:
                latest = (project.get("versions") or [{}])[-1].get("snapshot", {})
                merged = dict(latest)
                merged.update({k: v for k, v in project.items()
                               if k not in ("versions", "metadata", "createdAt", "updatedAt")})
                return merged
        return body.get("source") if isinstance(body.get("source"), dict) else body

    # ------------------------------------------------------------------ #
    # Capabilities / registries
    # ------------------------------------------------------------------ #

    def music_capabilities():
        user_id, error = _auth()
        if error:
            return error
        status = providers.provider_status()
        provider = providers.get_provider()
        caps = provider.capabilities()
        # Feature honesty: server_audio features are marked supported only when
        # the active provider can actually render; the UI keeps the controls.
        features = []
        for feat in MUSIC_FEATURES:
            item = dict(feat)
            if item["id"] in ("remix", "extend", "create", "track_generate", "vocal_lab"):
                item["provider_supported"] = bool(caps.get("generate") or caps.get("remix")
                                                  or caps.get("extend") or caps.get("vocal_ai"))
            elif item["id"] == "stems":
                item["provider_supported"] = bool(caps.get("stems"))
            elif item["id"] == "master":
                item["provider_supported"] = bool(caps.get("master"))
            elif item["id"] == "audio_tools":
                item["provider_supported"] = audio.available()
            else:
                item["provider_supported"] = True
            features.append(item)
        return jsonify({
            "status": "success",
            "provider": status.get("provider"),
            "creative": status.get("creative"),
            "audio": status.get("audio"),
            "server_audio_configured": status.get("server_audio_configured"),
            "notes": status.get("notes"),
            "features": features,
            "audio_processor": {
                "available": audio.available(),
                "tools": audio.tool_specs(),
                "quick_stems": audio.available(),
                "vocal_clean": audio.available(),
                "loudness": audio.available(),
                "convert_formats": ["wav", "mp3"],
            },
            "genres": african.genre_list(),
            "languages": african.LYRIC_LANGUAGES,
            "keys": african.KEYS,
            "remix_presets": REMIX_PRESETS,
            "master_targets": MASTER_TARGETS,
        })

    def music_genres():
        return jsonify({
            "status": "success",
            "genres": african.genre_list(),
            "languages": african.LYRIC_LANGUAGES,
            "keys": african.KEYS,
            "remix_presets": REMIX_PRESETS,
            "master_targets": MASTER_TARGETS,
        })

    # ------------------------------------------------------------------ #
    # Create / generate
    # ------------------------------------------------------------------ #

    def music_generate():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        brief = str(body.get("brief") or body.get("description") or "").strip()
        lyrics = str(body.get("lyrics") or "").strip()
        if not brief and not lyrics:
            return _err("Describe your song or add lyrics first.", 400, "missing_brief")
        project_id = str(body.get("project_id") or "").strip()
        instrumental = bool(body.get("instrumental")) or str(body.get("mode", "")).lower() == "instrumental"

        settings = {
            "role": str(body.get("role") or "singer"),
            "genre": str(body.get("genre") or "Afrobeats"),
            "subgenre": str(body.get("subgenre") or ""),
            "mood": str(body.get("mood") or ""),
            "tempo": str(body.get("tempo") or "medium"),
            "key": str(body.get("key") or ""),
            "language": str(body.get("language") or "English"),
            "voice": str(body.get("voice") or ""),
            "bpm": body.get("bpm"),
            "structure": str(body.get("structure") or ""),
            "energy": body.get("energy"),
            "instrumental": instrumental,
            "negative": str(body.get("negative_prompt") or body.get("negative") or ""),
            "duration": body.get("duration"),
            "time_signature": str(body.get("time_signature") or ""),
            "lyrics_mode": str(body.get("lyrics_mode") or ""),
        }

        def _run(progress, is_cancelled):
            progress(None)
            pkg = engine.produce_package(brief=brief, lyrics=lyrics, **settings)
            if pkg.get("status") != "success":
                raise MusicError(pkg.get("error") or "Generation failed.", code="produce_failed")
            audio = _try_provider("render_track", {
                "kind": "generate", "brief": brief, "settings": settings,
                "package": pkg.get("data", {}),
            })
            result = {"package": pkg, "audio": audio, "brief": brief, "settings": settings}
            if project_id:
                updated = projects.add_version(
                    user_id, project_id, label=body.get("version_label") or "",
                    package={"package": pkg, "settings": settings, "audio": audio},
                    note="AI generation")
                if updated:
                    result["project"] = updated["project"]
                    result["version"] = updated["version"]
            return result

        job = jobs.submit(user_id, "generate", _run,
                          meta={"project_id": project_id, "genre": settings["genre"]})
        return jsonify({"status": "success", "job": job}), 202

    def music_track_generate():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        track_type = str(body.get("track_type") or body.get("instrument") or "drums").strip()
        project_id = str(body.get("project_id") or "").strip()

        def _run(progress, is_cancelled):
            progress(None)
            pkg = engine.produce_track(
                track_type,
                brief=str(body.get("brief") or ""),
                genre=str(body.get("genre") or "Afrobeats"),
                bpm=body.get("bpm"),
                key=str(body.get("key") or ""),
                bars=int(body.get("bars") or 8),
                mood=str(body.get("mood") or ""),
                detail=str(body.get("detail") or ""),
            )
            if pkg.get("status") != "success":
                raise MusicError(pkg.get("error") or "Track generation failed.",
                                 code="produce_failed")
            audio = _try_provider("render_track", {
                "kind": "track", "track_type": track_type, "package": pkg.get("data", {}),
            })
            result = {"package": pkg, "track_type": track_type, "audio": audio}
            if project_id:
                updated = projects.add_version(
                    user_id, project_id, label="", package=result,
                    note=f"Generated {track_type} track")
                if updated:
                    result["project"] = updated["project"]
                    result["version"] = updated["version"]
            return result

        job = jobs.submit(user_id, "track_generate", _run,
                          meta={"track_type": track_type, "project_id": project_id})
        return jsonify({"status": "success", "job": job}), 202

    # ------------------------------------------------------------------ #
    # Reference / Inspire
    # ------------------------------------------------------------------ #

    def music_reference():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        reference = str(body.get("reference") or body.get("name") or "").strip()
        genre_hint = str(body.get("genre_hint") or body.get("genre") or "").strip()
        user_notes = str(body.get("user_notes") or body.get("notes") or "").strip()
        analysis = body.get("analysis") if isinstance(body.get("analysis"), dict) else {}
        create = bool(body.get("create_project"))
        uploaded = None
        file = request.files.get("file") if request.files else None
        if file and file.filename:
            uploaded = storage.save_stream(user_id, "reference", file,
                                           filename=file.filename)
            if not reference:
                reference = f"Uploaded reference: {uploaded['name']}"
        rel = str(body.get("rel") or "").strip()
        if rel and not uploaded:
            try:
                storage.user_file_path(user_id, rel, must_exist=True)
                uploaded = {"rel": rel, "name": rel.split("/")[-1]}
            except storage.StorageError:
                return _err("Reference file not found.", 404, "not_found")

        def _run(progress, is_cancelled):
            progress(None)
            result = ops.analyze_reference(reference, genre_hint=genre_hint,
                                           analysis=analysis, user_notes=user_notes)
            if result.get("status") != "success":
                raise MusicError(result.get("error") or "Reference analysis failed.",
                                 code="reference_failed")
            result["uploaded"] = uploaded
            # Original-music guarantee is explicit in the blueprint.
            result["originality"] = ("This produces an ORIGINAL composition in the "
                                     "reference's lane. No copyrighted audio is "
                                     "reproduced or ripped.")
            if create:
                blueprint = result.get("blueprint") or {}
                project = projects.create_project(user_id, {
                    "name": blueprint.get("title") or (reference[:60] or "Reference track"),
                    "brief": user_notes or f"Inspired by {reference}",
                    "genre": blueprint.get("genre") or genre_hint,
                    "bpm": blueprint.get("bpm"),
                    "key": blueprint.get("key"),
                    "structure": blueprint.get("structure"),
                    "instruments": blueprint.get("instruments"),
                    "reference": {"name": reference, "analysis": result.get("analysis")},
                })
                result["project"] = project
            return result

        job = jobs.submit(user_id, "reference", _run, meta={"reference": reference})
        return jsonify({"status": "success", "job": job}), 202

    # ------------------------------------------------------------------ #
    # Remix / Extend
    # ------------------------------------------------------------------ #

    def music_remix():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        body["_user_id"] = user_id
        project_id = str(body.get("project_id") or "").strip()
        source = _source_package_from(body)
        instruction = str(body.get("instruction") or "").strip()
        genre = str(body.get("genre") or "").strip()
        mood = str(body.get("mood") or "").strip()
        tempo = str(body.get("tempo") or "").strip()
        as_new = body.get("as_new_project", True)

        def _run(progress, is_cancelled):
            progress(None)
            result = ops.make_remix_plan(source, instruction=instruction,
                                         genre=genre, mood=mood, tempo=tempo)
            if result.get("status") != "success":
                raise MusicError(result.get("error") or "Remix failed.", code="remix_failed")
            audio = _try_provider("remix_track", {"source": source, "plan": result.get("data", {})})
            result["audio"] = audio
            plan = result.get("data") or {}
            created = None
            if project_id and not as_new:
                created = projects.add_version(
                    user_id, project_id, label="", package=result, note="AI remix")
                if created:
                    result["project"] = created["project"]
                    result["version"] = created["version"]
            else:
                base_name = str(source.get("title") or source.get("name") or "Remix")
                created = projects.create_project(user_id, {
                    "name": f"{base_name} — {genre or instruction or 'Remix'}"[:80],
                    "brief": instruction or f"Remix of {base_name}",
                    "genre": genre or source.get("genre"),
                    "mood": mood or source.get("mood"),
                    "bpm": plan.get("bpm") or source.get("bpm"),
                    "key": plan.get("key") or source.get("key"),
                    "structure": plan.get("structure"),
                    "lyrics": source.get("lyrics", ""),
                    "source_project_id": project_id,
                    "remix": {"instruction": instruction, "genre": genre},
                    "stems": source.get("stems") or [],
                })
                result["project"] = created
            return result

        job = jobs.submit(user_id, "remix", _run, meta={"project_id": project_id})
        return jsonify({"status": "success", "job": job}), 202

    def music_extend():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        body["_user_id"] = user_id
        project_id = str(body.get("project_id") or "").strip()
        source = _source_package_from(body)
        target_seconds = body.get("target_seconds") or body.get("seconds") or 0
        instruction = str(body.get("instruction") or "").strip()
        try:
            target_seconds = int(float(target_seconds or 0))
        except (TypeError, ValueError):
            target_seconds = 0

        def _run(progress, is_cancelled):
            progress(None)
            result = ops.make_extend_plan(source, target_seconds=target_seconds,
                                          instruction=instruction)
            if result.get("status") != "success":
                raise MusicError(result.get("error") or "Extend failed.", code="extend_failed")
            audio = _try_provider("extend_track", {"source": source, "plan": result.get("data", {})})
            result["audio"] = audio
            if project_id:
                created = projects.add_version(
                    user_id, project_id, label="", package=result, note="AI extension")
                if created:
                    result["project"] = created["project"]
                    result["version"] = created["version"]
            return result

        job = jobs.submit(user_id, "extend", _run,
                          meta={"project_id": project_id, "target_seconds": target_seconds})
        return jsonify({"status": "success", "job": job}), 202

    # ------------------------------------------------------------------ #
    # Stem separation
    # ------------------------------------------------------------------ #

    def music_stems():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        project_id = str(body.get("project_id") or "").strip()
        file = request.files.get("file") if request.files else None
        uploaded = None
        if file and file.filename:
            uploaded = storage.save_stream(user_id, "uploads", file, filename=file.filename)
        else:
            rel = str(body.get("rel") or "").strip()
            if rel:
                try:
                    uploaded = {"rel": rel, "name": rel.split("/")[-1]}
                    storage.user_file_path(user_id, rel, must_exist=True)
                except storage.StorageError:
                    return _err("Audio file not found.", 404, "not_found")
        if not uploaded:
            return _err("Upload an audio file to separate.", 400, "missing_audio")
        stems_wanted = body.get("stems") or ["vocals", "drums", "bass", "melody", "other"]

        def _run(progress, is_cancelled):
            progress(None)
            audio = _try_provider("separate_stems", {
                "audio": uploaded, "stems": stems_wanted, "project_id": project_id,
            })
            # Stem separation has no creative fallback: if no provider can render
            # the stems, the operation genuinely failed — say so, don't fake it.
            if not audio.get("rendered"):
                if audio.get("unavailable"):
                    raise ProviderUnavailable(
                        audio.get("note") or "No stem-separation provider is configured.",
                        capability="stems")
                raise MusicError(audio.get("error") or "Stem separation failed.",
                                 code=audio.get("error_code") or "stems_failed")
            result = {"source": uploaded, "requested_stems": stems_wanted, "audio": audio}
            if project_id:
                project = projects.get_project(user_id, project_id)
                if project is not None:
                    stems = list(project.get("stems") or [])
                    stems.extend(audio.get("stems") or [])
                    projects.update_project(user_id, project_id, {"stems": stems})
                    result["project"] = projects.get_project(user_id, project_id)
            return result

        job = jobs.submit(user_id, "stems", _run, meta={"project_id": project_id})
        return jsonify({"status": "success", "job": job}), 202

    # ------------------------------------------------------------------ #
    # Vocal Lab
    # ------------------------------------------------------------------ #

    def music_vocal_process():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        project_id = str(body.get("project_id") or "").strip()
        file = request.files.get("file") if request.files else None
        uploaded = None
        if file and file.filename:
            uploaded = storage.save_stream(user_id, "vocals", file, filename=file.filename)
        else:
            rel = str(body.get("rel") or "").strip()
            if rel:
                try:
                    storage.user_file_path(user_id, rel, must_exist=True)
                    uploaded = {"rel": rel, "name": rel.split("/")[-1]}
                except storage.StorageError:
                    return _err("Vocal file not found.", 404, "not_found")
        analysis = body.get("analysis") if isinstance(body.get("analysis"), dict) else {}
        genre = str(body.get("genre") or "Afrobeats")
        mood = str(body.get("mood") or "")
        structure = str(body.get("structure") or "")
        key_override = str(body.get("key_override") or "")
        style_text = str(body.get("production_style") or "")
        clean = bool(body.get("clean", True))

        def _run(progress, is_cancelled):
            progress(None)
            plan = engine.make_production_plan(analysis, genre=genre, mood=mood,
                                               style_text=style_text,
                                               structure=structure,
                                               key_override=key_override)
            if plan.get("status") != "success":
                raise MusicError(plan.get("error") or "Vocal plan failed.", code="vocal_failed")
            provider_audio = _try_provider("vocal_produce", {
                "vocal": uploaded, "analysis": analysis, "genre": genre,
                "mood": mood, "clean": clean,
            })
            # The polished-song promise is only made when the provider really
            # rendered; otherwise we are explicit. The production plan is real.
            return {
                "plan": plan.get("plan", {}),
                "vocal": uploaded,
                "audio": provider_audio,
                "polished_song": bool(provider_audio.get("rendered")),
                "note": (provider_audio.get("note") or
                         "Production plan ready. Render the instrumental in your "
                         "browser or configure a music provider for a full mix."),
            }

        job = jobs.submit(user_id, "vocal_process", _run,
                          meta={"project_id": project_id, "genre": genre})
        return jsonify({"status": "success", "job": job}), 202

    def music_vocal_refine():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        instruction = str(body.get("instruction") or "").strip()
        if not instruction:
            return _err("Instruction is required.", 400, "missing_instruction")
        plan = body.get("plan") if isinstance(body.get("plan"), dict) else {}
        result = engine.refine_production_plan(instruction, plan,
                                               genre=str(body.get("genre") or ""))
        if result.get("status") != "success":
            return _err(result.get("error") or "Refine failed.", 502, "refine_failed")
        return jsonify({"status": "success", "changes": result.get("changes", {}),
                        "summary": result.get("summary", "")})

    # ------------------------------------------------------------------ #
    # Mastering
    # ------------------------------------------------------------------ #

    def music_master():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        project_id = str(body.get("project_id") or "").strip()
        target = str(body.get("target") or "streaming").strip().lower()
        target_info = next((t for t in MASTER_TARGETS if t["id"] == target),
                           MASTER_TARGETS[0])
        mix = body.get("mix") if isinstance(body.get("mix"), dict) else {}
        instruction = str(body.get("instruction") or "").strip()
        rel = str(body.get("rel") or "").strip()

        def _run(progress, is_cancelled):
            progress(None)
            notes = ops.master_notes(mix=mix, instruction=instruction)
            if notes.get("status") != "success":
                raise MusicError(notes.get("error") or "Mastering analysis failed.",
                                 code="master_failed")
            audio = _try_provider("master_track", {
                "rel": rel, "target": target_info, "mix": mix,
                "project_id": project_id,
            })
            result = {
                "target": target_info,
                "mastering": notes.get("mastering", {}),
                "audio": audio,
                "mastered": bool(audio.get("rendered")),
                "note": (audio.get("note") or
                         "These are real AI mastering recommendations for your mix. "
                         "A mastered audio file requires a configured provider."),
            }
            if project_id:
                updated = projects.add_version(user_id, project_id, label="",
                                               package=result, note=f"Master target: {target}")
                if updated:
                    result["project"] = updated["project"]
                    result["version"] = updated["version"]
            return result

        job = jobs.submit(user_id, "master", _run, meta={"target": target})
        return jsonify({"status": "success", "job": job}), 202

    # ------------------------------------------------------------------ #
    # Real audio tools (ffmpeg: processing, vocal cleanup, quick stems,
    # loudness, format conversion)
    # ------------------------------------------------------------------ #

    def _resolve_audio_rel(user_id) -> str | None:
        """Pick the audio source: uploaded file > ``rel`` in the request body."""
        file = request.files.get("file") if request.files else None
        if file and file.filename:
            uploaded = storage.save_stream(user_id, "uploads", file, filename=file.filename)
            return uploaded["rel"]
        rel = str(_body().get("rel") or "").strip()
        if rel:
            try:
                storage.user_file_path(user_id, rel, must_exist=True)
            except storage.StorageError:
                pass
            return rel
        return None

    def _audio_ready():
        if not audio.available():
            return _err("Real audio processing requires the bundled ffmpeg "
                        "processor, which is not available on this server.",
                        503, "audio_unavailable")
        return None

    def music_audio_tools():
        user_id, error = _auth()
        if error:
            return error
        blocked = _audio_ready()
        if blocked:
            return blocked
        rel = _resolve_audio_rel(user_id)
        if not rel:
            return _err("Choose a stored file or upload audio to process.",
                        400, "missing_audio")
        tools = _body().get("tools")
        if not isinstance(tools, list) or not tools:
            return _err("Select at least one audio tool.", 400, "missing_tools")
        result = audio.apply_tools(user_id, rel, tools)
        if result.get("status") != "success":
            return _err(result.get("error") or "Processing failed.", 400,
                        result.get("error_code") or "process_failed",
                        detail=result.get("detail"))
        return jsonify({"status": "success", **result})

    def music_vocal_clean():
        user_id, error = _auth()
        if error:
            return error
        blocked = _audio_ready()
        if blocked:
            return blocked
        rel = str(_body().get("rel") or "").strip()
        file = request.files.get("file") if request.files else None
        if file and file.filename:
            uploaded = storage.save_stream(user_id, "vocals", file, filename=file.filename)
            rel = uploaded["rel"]
        if not rel:
            return _err("Choose a stored vocal file to clean.", 400, "missing_audio")
        opts = _body().get("opts") if isinstance(_body().get("opts"), dict) else {}
        result = audio.process_vocal(user_id, rel, opts)
        if result.get("status") != "success":
            return _err(result.get("error") or "Processing failed.", 400,
                        result.get("error_code") or "process_failed")
        return jsonify({"status": "success", **result})

    def music_stems_quick():
        user_id, error = _auth()
        if error:
            return error
        blocked = _audio_ready()
        if blocked:
            return blocked
        rel = _resolve_audio_rel(user_id)
        if not rel:
            return _err("Choose a stored file to split.", 400, "missing_audio")
        result = audio.quick_stems(user_id, rel)
        if result.get("status") != "success":
            return _err(result.get("error") or "Stem split failed.", 400,
                        result.get("error_code") or "process_failed")
        return jsonify({"status": "success", **result})

    def music_loudness():
        user_id, error = _auth()
        if error:
            return error
        blocked = _audio_ready()
        if blocked:
            return blocked
        rel = _resolve_audio_rel(user_id)
        if not rel:
            return _err("Choose a stored file to analyse.", 400, "missing_audio")
        try:
            report = audio.loudness(user_id, rel)
        except storage.StorageError:
            return _err("Audio file not found.", 404, "not_found")
        return jsonify({"status": "success", "loudness": report})

    # ------------------------------------------------------------------ #
    # Lyrics Lab
    # ------------------------------------------------------------------ #

    def music_lyrics():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        action = str(body.get("action") or "generate").strip().lower()
        result = engine.lyrics_lab(
            str(body.get("lyrics") or body.get("text") or ""),
            action=action,
            instruction=str(body.get("instruction") or ""),
            language=str(body.get("language") or "English"),
            target_language=str(body.get("target_language") or ""),
            genre=str(body.get("genre") or "Afrobeats"),
            mood=str(body.get("mood") or ""),
            title=str(body.get("title") or ""),
        )
        if result.get("status") != "success":
            return _err(result.get("error") or "Lyrics generation failed.",
                        502, "lyrics_failed")
        project_id = str(body.get("project_id") or "").strip()
        if project_id:
            updated = projects.update_project(user_id, project_id,
                                              {"lyrics": result.get("lyrics", "")})
            if updated:
                result["project"] = updated
        return jsonify({"status": "success", **result})

    # ------------------------------------------------------------------ #
    # Elena Music Co-Pilot
    # ------------------------------------------------------------------ #

    def music_copilot():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        message = str(body.get("message") or "").strip()
        if not message:
            return _err("Say something to Elena.", 400, "missing_message")
        context = body.get("context") if isinstance(body.get("context"), dict) else {}
        history = body.get("history") if isinstance(body.get("history"), list) else []
        result = ops.elena_copilot(message, context=context, history=history)
        if result.get("status") != "success":
            return _err(result.get("error") or "Elena could not answer.", 502, "copilot_failed")
        # Return the structured operation the client should execute next; the
        # backend never pretends an operation already happened.
        result["operation"] = {
            "intent": result.get("intent"),
            "params": result.get("params", {}),
            "endpoint": result.get("suggested_action", ""),
        }
        return jsonify({"status": "success", **result})

    # ------------------------------------------------------------------ #
    # Jobs
    # ------------------------------------------------------------------ #

    def music_job_get(job_id):
        user_id, error = _auth()
        if error:
            return error
        job = jobs.get(user_id, job_id)
        if not job:
            return _err("Job not found.", 404, "not_found")
        return jsonify({"status": "success", "job": job})

    def music_job_cancel(job_id):
        user_id, error = _auth()
        if error:
            return error
        ok = jobs.cancel(user_id, job_id)
        if not ok:
            return _err("Job cannot be cancelled.", 409, "not_cancellable")
        return jsonify({"status": "success", "job": jobs.get(user_id, job_id)})

    def music_job_list():
        user_id, error = _auth()
        if error:
            return error
        return jsonify({"status": "success", "jobs": jobs.list_for_user(user_id)})

    # ------------------------------------------------------------------ #
    # Projects + versions + library
    # ------------------------------------------------------------------ #

    def music_project_get(project_id):
        user_id, error = _auth()
        if error:
            return error
        project = projects.get_project(user_id, project_id)
        if not project:
            return _err("Project not found.", 404, "not_found")
        return jsonify({"status": "success", "project": project})

    def music_project_put(project_id):
        user_id, error = _auth()
        if error:
            return error
        project = projects.update_project(user_id, project_id, _body())
        if not project:
            return _err("Project not found.", 404, "not_found")
        return jsonify({"status": "success", "project": project})

    def music_project_create():
        user_id, error = _auth()
        if error:
            return error
        project = projects.create_project(user_id, _body())
        return jsonify({"status": "success", "project": project})

    def music_project_duplicate(project_id):
        user_id, error = _auth()
        if error:
            return error
        clone = projects.duplicate_project(user_id, project_id,
                                           name=str(_body().get("name") or ""))
        if not clone:
            return _err("Project not found.", 404, "not_found")
        return jsonify({"status": "success", "project": clone})

    def music_project_versions(project_id):
        user_id, error = _auth()
        if error:
            return error
        if request.method == "GET":
            versions = projects.list_versions(user_id, project_id)
            if versions is None:
                return _err("Project not found.", 404, "not_found")
            return jsonify({"status": "success", "versions": versions})
        body = _body()
        result = projects.add_version(user_id, project_id,
                                      label=str(body.get("label") or ""),
                                      package=body.get("package") if isinstance(
                                          body.get("package"), dict) else None,
                                      note=str(body.get("note") or ""))
        if not result:
            return _err("Project not found.", 404, "not_found")
        return jsonify({"status": "success", **result})

    def music_project_restore(project_id):
        user_id, error = _auth()
        if error:
            return error
        version_id = str(_body().get("version_id") or "").strip()
        if not version_id:
            return _err("version_id is required.", 400, "missing_version")
        project = projects.restore_version(user_id, project_id, version_id)
        if not project:
            return _err("Project or version not found.", 404, "not_found")
        return jsonify({"status": "success", "project": project})

    def music_library():
        user_id, error = _auth()
        if error:
            return error
        query = str(request.args.get("q") or "").strip()
        category = str(request.args.get("category") or "").strip().lower()
        items = projects.library(user_id, query=query, category=category)
        return jsonify({"status": "success", "items": items, "count": len(items)})

    # ------------------------------------------------------------------ #
    # File streaming (auth-scoped) + export manifest
    # ------------------------------------------------------------------ #

    def music_file(rel):
        user_id, error = _auth()
        if error:
            return error
        try:
            path = storage.user_file_path(user_id, rel, must_exist=True)
        except storage.StorageError:
            return _err("File not found.", 404, "not_found")
        return send_file(str(path), conditional=True)

    def music_export():
        user_id, error = _auth()
        if error:
            return error
        body = _body()
        project_id = str(body.get("project_id") or "").strip()
        fmt = str(body.get("format") or "wav").strip().lower()
        project = projects.get_project(user_id, project_id) if project_id else None
        provider = providers.get_provider()
        caps = provider.capabilities()

        converted = None
        convert_rel = str(body.get("rel") or "").strip()
        if convert_rel and fmt in ("wav", "mp3"):
            if not audio.available():
                converted = {"status": "error", "error": "Audio processor (ffmpeg) "
                            "is not available on this server.",
                            "error_code": "audio_unavailable"}
            else:
                try:
                    converted = audio.convert(user_id, convert_rel, fmt)
                except storage.StorageError:
                    converted = {"status": "error", "error": "File not found.",
                                 "error_code": "not_found"}

        files = storage.list_files(user_id)
        audio_files = [f for f in files if f["category"] in
                       ("generated", "stems", "vocals", "master", "exports", "uploads")]
        available = {
            "wav": bool(converted and converted.get("status") == "success") or
                   any(f["rel"].lower().endswith(".wav") for f in audio_files),
            "mp3": bool(converted and converted.get("status") == "success") or
                   any(f["rel"].lower().endswith(".mp3") for f in audio_files),
            "stems": bool(project and project.get("stems")),
            "instrumental": bool(caps.get("generate")),
            "vocals": bool(caps.get("vocal_ai")),
            "project_package": True,
            "conversion": audio.available(),
        }
        return jsonify({
            "status": "success",
            "format": fmt,
            "files": audio_files,
            "available": available,
            "converted": converted,
            "note": ("Server-rendered exports require a configured provider. "
                     "Stored audio can be converted to WAV/MP3 here for real, "
                     "and a project package is always exportable."),
        })

    # ------------------------------------------------------------------ #
    # Route table (non-colliding with app.py's existing /api/music routes)
    # ------------------------------------------------------------------ #

    app.add_url_rule("/api/music/capabilities", "music_capabilities",
                     music_capabilities, methods=["GET"])
    app.add_url_rule("/api/music/genres", "music_genres", music_genres, methods=["GET"])
    app.add_url_rule("/api/music/generate", "music_generate", music_generate, methods=["POST"])
    app.add_url_rule("/api/music/track/generate", "music_track_generate",
                     music_track_generate, methods=["POST"])
    app.add_url_rule("/api/music/reference/analyze", "music_reference",
                     music_reference, methods=["POST"])
    app.add_url_rule("/api/music/reference", "music_reference_alias",
                     music_reference, methods=["POST"])
    app.add_url_rule("/api/music/remix", "music_remix", music_remix, methods=["POST"])
    app.add_url_rule("/api/music/extend", "music_extend", music_extend, methods=["POST"])
    app.add_url_rule("/api/music/stems/separate", "music_stems", music_stems, methods=["POST"])
    app.add_url_rule("/api/music/stems", "music_stems_alias", music_stems, methods=["POST"])
    app.add_url_rule("/api/music/vocal/process", "music_vocal_process",
                     music_vocal_process, methods=["POST"])
    app.add_url_rule("/api/music/vocal/refine", "music_vocal_refine",
                     music_vocal_refine, methods=["POST"])
    app.add_url_rule("/api/music/master", "music_master", music_master, methods=["POST"])
    app.add_url_rule("/api/music/lyrics", "music_lyrics", music_lyrics, methods=["POST"])
    app.add_url_rule("/api/music/copilot", "music_copilot", music_copilot, methods=["POST"])
    app.add_url_rule("/api/music/jobs", "music_job_list", music_job_list, methods=["GET"])
    app.add_url_rule("/api/music/jobs/<job_id>", "music_job_get", music_job_get, methods=["GET"])
    app.add_url_rule("/api/music/jobs/<job_id>/cancel", "music_job_cancel",
                     music_job_cancel, methods=["POST"])
    app.add_url_rule("/api/music/workstation/projects", "music_project_create",
                     music_project_create, methods=["POST"])
    app.add_url_rule("/api/music/projects/<project_id>", "music_project_get",
                     music_project_get, methods=["GET"])
    app.add_url_rule("/api/music/projects/<project_id>", "music_project_put",
                     music_project_put, methods=["PUT"])
    app.add_url_rule("/api/music/projects/<project_id>/duplicate", "music_project_duplicate",
                     music_project_duplicate, methods=["POST"])
    app.add_url_rule("/api/music/projects/<project_id>/versions", "music_project_versions",
                     music_project_versions, methods=["GET", "POST"])
    app.add_url_rule("/api/music/projects/<project_id>/restore", "music_project_restore",
                     music_project_restore, methods=["POST"])
    app.add_url_rule("/api/music/library", "music_library", music_library, methods=["GET"])
    app.add_url_rule("/api/music/files/<path:rel>", "music_file", music_file, methods=["GET"])
    app.add_url_rule("/api/music/export", "music_export", music_export, methods=["POST"])
    app.add_url_rule("/api/music/audio/tools", "music_audio_tools",
                     music_audio_tools, methods=["POST"])
    app.add_url_rule("/api/music/vocal/clean", "music_vocal_clean",
                     music_vocal_clean, methods=["POST"])
    app.add_url_rule("/api/music/stems/quick", "music_stems_quick",
                     music_stems_quick, methods=["POST"])
    app.add_url_rule("/api/music/loudness", "music_loudness",
                     music_loudness, methods=["POST"])
    app.add_url_rule("/api/music/projects", "music_project_create_alias",
                     music_project_create, methods=["POST"])

    return app
