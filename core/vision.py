"""Unified vision integration for ValleyMind-AI.

This module provides a shared interface for image analysis across all personas
(Marcus, Angelina, Elena) and the floating companion. It integrates with the
existing brain/chat flow without creating duplicate systems.

Features:
- Analyze user-supplied images, camera frames, screen share frames
- Describe objects, text, scenes, actions
- Provider-agnostic with cost controls and graceful fallbacks
- Server-side processing only, never exposes API keys to clients
- Treats image data as untrusted
- Respects persist_image_data and auth checks
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.config import get_config


@dataclass
class VisionResult:
    """Structured result from vision analysis."""
    success: bool
    description: str = ""
    objects: List[str] = None
    text_content: str = ""
    scene: str = ""
    confidence: float = 0.0
    provider: str = ""
    error: str = ""
    raw: Dict[str, Any] = None
    images_analyzed: int = 0
    videos_analyzed: int = 0
    video_analyzed: bool = False

    def __post_init__(self):
        if self.objects is None:
            self.objects = []
        if self.raw is None:
            self.raw = {}


class VisionService:
    """Unified vision service for all personas and companion."""
    
    MAX_IMAGE_SIZE_BYTES = 4 * 1024 * 1024  # 4MB
    MAX_IMAGE_DIMENSION = 2048
    ALLOWED_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}

    # ── NVIDIA Nemotron 3.5 Super VL specific limits ───────────────────
    # Nemotron 3.5 Super VL supports native images and video via the
    # OpenAI-compatible chat/completions endpoint.  There is no published
    # hard cap on image count; 16 is a conservative default that leaves
    # ample headroom for text + reasoning tokens within the 1M-token
    # context window.  Override via NVIDIA_MAX_IMAGES_PER_REQUEST.
    NVIDIA_MAX_IMAGES_PER_REQUEST = int(
        os.getenv("NVIDIA_MAX_IMAGES_PER_REQUEST", "16")
    )
    NVIDIA_MAX_IMAGE_SIZE_BYTES = int(
        os.getenv("NVIDIA_MAX_IMAGE_SIZE_MB", "10")
    ) * 1024 * 1024  # 10 MB per image (decoded, before base64)
    NVIDIA_MAX_VIDEO_SIZE_BYTES = int(
        os.getenv("NVIDIA_MAX_VIDEO_SIZE_MB", "100")
    ) * 1024 * 1024  # 100 MB per video
    # Above this size a phone video is downscaled before being inlined as
    # base64: a 100MB clip becomes a ~133MB JSON body that upstream rejects.
    # Override with NVIDIA_MAX_INLINE_VIDEO_MB when running with a live key.
    NVIDIA_MAX_INLINE_VIDEO_BYTES = int(
        os.getenv("NVIDIA_MAX_INLINE_VIDEO_MB", "24")
    ) * 1024 * 1024
    NVIDIA_MAX_VIDEO_SECONDS = int(
        os.getenv("NVIDIA_MAX_VIDEO_SECONDS", "120")
    )  # 2 minutes
    NVIDIA_ALLOWED_VIDEO_MIME_TYPES = {
        "video/mp4", "video/webm", "video/mov", "video/ogg",
    }
    # Nemotron 3.5 Super VL is the configured NVIDIA vision model.
    NVIDIA_NEMOTRON_MODEL = os.getenv(
        "NVIDIA_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b"
    ).strip()

    def __init__(self):
        self.config = get_config()
        self._last_cost = 0.0
        self.activity_events: list = []
    
    def _emit_activity(self, category: str, key: str, label: Optional[str] = None, detail: str = ""):
        """Record a vision activity event (non-yielding, stored for inspection)."""
        ev = {
            "category": category,
            "key": key,
            "label": label or f"{key.replace('_', ' ').title()}…",
            "detail": detail,
            "timestamp": time.time(),
        }
        self.activity_events.append(ev)
        return ev
        """Validate image data URI. Returns (valid, error_message)."""
        if not image_data or not isinstance(image_data, str):
            return False, "No image data provided"
        
        text = image_data.strip()
        if not text.lower().startswith("data:image/"):
            return False, "Invalid image format - must be data URI"
        if ";base64," not in text:
            return False, "Invalid image format - missing base64"
        if len(text) > self.MAX_IMAGE_SIZE_BYTES:
            return False, f"Image too large (max {self.MAX_IMAGE_SIZE_BYTES // 1024 // 1024}MB)"
        
        try:
            header, b64_data = text.split(",", 1)
            mime = header.split(";")[0].replace("data:", "").lower()
            if mime not in self.ALLOWED_MIME_TYPES and mime != "image/*":
                # Be lenient but log
                pass
            # Validate base64
            base64.b64decode(b64_data, validate=True)
            return True, ""
        except Exception as e:
            return False, f"Invalid image data: {str(e)[:100]}"
    
    def prepare_image(self, image_data: str, max_dim: int = None) -> Optional[str]:
        """Prepare/optimize image for vision API. Returns optimized data URI or original if processing fails."""
        max_dim = max_dim or self.MAX_IMAGE_DIMENSION
        try:
            from PIL import Image
            valid, err = self.validate_image(image_data)
            if not valid:
                return None
            
            header, b64_data = image_data.split(",", 1)
            img_bytes = base64.b64decode(b64_data)
            img = Image.open(io.BytesIO(img_bytes))
            
            # Convert to RGB if necessary
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            
            # Resize if too large
            if max(img.size) > max_dim:
                ratio = max_dim / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Save as JPEG with compression
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=85, optimize=True)
            new_b64 = base64.b64encode(output.getvalue()).decode('utf-8')
            return f"data:image/jpeg;base64,{new_b64}"
        except ImportError:
            # PIL not available, return original if valid
            valid, _ = self.validate_image(image_data)
            return image_data if valid else None
        except Exception as e:
            print(f"[VISION] Image preparation failed: {e}")
            valid, _ = self.validate_image(image_data)
            return image_data if valid else None
    
    def _get_vision_provider(self) -> Tuple[Optional[str], Optional[Dict]]:
        """Find a configured vision-capable provider. Returns (provider_name, config)."""
        # Prefer providers with likely working free vision models
        # OpenRouter often has free vision models
        if self.config.openrouter_api_key:
            return ("openrouter", {"api_key": self.config.openrouter_api_key, "base_url": self.config.openrouter_base_url or "https://openrouter.ai/api/v1"})
        
        # Gemini has good free tier
        if self.config.gemini_api_key:
            return ("gemini", {"api_key": self.config.gemini_api_key, "base_url": self.config.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta"})
        
        # NVIDIA
        if self.config.nvidia_api_key:
            return ("nvidia", {"api_key": self.config.nvidia_api_key, "base_url": self.config.nvidia_base_url or "https://integrate.api.nvidia.com/v1"})
        
        # Groq last (vision models may be deprecated)
        if self.config.groq_api_key:
            return ("groq", {"api_key": self.config.groq_api_key, "base_url": self.config.groq_base_url})
        
        return (None, None)

    def _get_nvidia_config(self) -> Optional[Dict]:
        """Resolve NVIDIA credentials explicitly.

        Video (and every Nemotron-specific call) must never inherit whichever
        provider happens to come first in the image preference order: sending
        an OpenRouter/Gemini key to NVIDIA's endpoint can only fail. When no
        NVIDIA key is configured we still accept an already-NVIDIA provider
        selection, which keeps existing callers and tests working.
        """
        key = str(getattr(self.config, "nvidia_api_key", "") or "").strip()
        if key:
            return {
                "api_key": key,
                "base_url": self.config.nvidia_base_url or "https://integrate.api.nvidia.com/v1",
            }
        try:
            provider_name, provider_config = self._get_vision_provider()
        except Exception:
            return None
        if provider_name == "nvidia" and provider_config:
            return provider_config
        return None
    
    # ── Server-side video preparation (multipart uploads) ──────────────
    VIDEO_EXT_MIME = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".ogv": "video/ogg",
        ".ogg": "video/ogg",
        ".mov": "video/quicktime",
        ".3gp": "video/3gpp",
        ".mkv": "video/x-matroska",
    }

    def video_mime_for(self, filename: str = "", mimetype: str = "") -> str:
        """Best-effort video MIME for an upload.

        Android/WebView uploads often arrive as application/octet-stream, so
        the filename extension is authoritative when the declared type is not
        a video type.
        """
        mime = str(mimetype or "").split(";")[0].strip().lower()
        if mime.startswith("video/"):
            return mime
        ext = os.path.splitext(str(filename or ""))[1].lower()
        return self.VIDEO_EXT_MIME.get(ext, "")

    def prepare_video_data_uri(
        self, video_path: str, filename: str = "", mimetype: str = ""
    ) -> Tuple[Optional[str], str]:
        """Build a ``data:video/...;base64,`` URI that Nemotron accepts.

        Containers NVIDIA does not accept (MOV/3GP/MKV) and clips too large to
        inline are converted to an H.264 MP4 analysis copy with the same
        ffmpeg the Alibaba path already uses. Never raises: returns
        ``(data_uri, error)``.
        """
        if not video_path or not os.path.exists(video_path):
            return None, "No video data provided."
        size = os.path.getsize(video_path)
        if size <= 0:
            return None, "The uploaded video is empty."
        if size > self.NVIDIA_MAX_VIDEO_SIZE_BYTES:
            return None, (
                f"Video too large ({size / 1024 / 1024:.1f}MB). "
                f"Maximum is {self.NVIDIA_MAX_VIDEO_SIZE_BYTES // 1024 // 1024}MB."
            )

        mime = self.video_mime_for(filename, mimetype)
        needs_conversion = mime not in self.NVIDIA_ALLOWED_VIDEO_MIME_TYPES
        needs_slimming = size > self.NVIDIA_MAX_INLINE_VIDEO_BYTES
        source = video_path
        scratch = ""
        try:
            if needs_conversion or needs_slimming:
                import subprocess
                import tempfile
                from core.video_assembly import ffmpeg_exe

                exe = ffmpeg_exe()
                if not exe:
                    if needs_conversion:
                        return None, (
                            f"Unsupported video format: {mime or 'unknown'}. "
                            "This server cannot convert it for analysis."
                        )
                    # Valid container, no converter: send the original bytes and
                    # let the provider decide.
                    source = video_path
                else:
                    # Unique per request: two conversions started in the same
                    # millisecond must never share (and truncate/remove) one
                    # scratch file. ffmpeg -y overwrites the empty placeholder.
                    _fd, scratch = tempfile.mkstemp(
                        prefix=f"nvidia_video_{os.getpid()}_", suffix=".mp4"
                    )
                    os.close(_fd)
                    cmd = [
                        exe, "-y", "-i", video_path,
                        "-t", str(self.NVIDIA_MAX_VIDEO_SECONDS),
                        "-vf", "fps=8,scale=-2:720", "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                        "-pix_fmt", "yuv420p", scratch,
                    ]
                    proc = subprocess.run(cmd, capture_output=True, timeout=300)
                    if (proc.returncode != 0 or not os.path.exists(scratch)
                            or os.path.getsize(scratch) <= 0):
                        if needs_conversion:
                            return None, "This video could not be prepared for analysis."
                    else:
                        source = scratch
                        mime = "video/mp4"
                        if os.path.getsize(source) > self.NVIDIA_MAX_VIDEO_SIZE_BYTES:
                            return None, "Video is still too large after preparation for analysis."

            with open(source, "rb") as fh:
                data = fh.read()
            if not data:
                return None, "The uploaded video is empty."
            return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii"), ""
        except Exception as exc:
            return None, f"Video preparation failed: {str(exc)[:150]}"
        finally:
            if scratch:
                try:
                    os.remove(scratch)
                except OSError:
                    pass

    def validate_image(self, image_data: str) -> Tuple[bool, str]:
        """Validate a single image data URI. Returns (valid, error_message)."""
        if not image_data or not isinstance(image_data, str):
            return False, "No image data provided"
        
        text = image_data.strip()
        if not text.lower().startswith("data:image/"):
            return False, "Invalid image format - must be data URI"
        if ";base64," not in text:
            return False, "Invalid image format - missing base64"
        if len(text) > self.MAX_IMAGE_SIZE_BYTES:
            return False, f"Image too large (max {self.MAX_IMAGE_SIZE_BYTES // 1024 // 1024}MB)"
        
        try:
            header, b64_data = text.split(",", 1)
            mime = header.split(";")[0].replace("data:", "").lower()
            # Be lenient but log
            base64.b64decode(b64_data, validate=True)
            return True, ""
        except Exception as e:
            return False, f"Invalid image data: {str(e)[:100]}"

    # ── Multi-image validation ─────────────────────────────────────────
    def analyze_image(self, image_data: str, question: str = "", context: str = "") -> VisionResult:
        """Analyze image with vision-capable model. Returns structured result."""
        valid, err = self.validate_image(image_data)
        if not valid:
            return VisionResult(success=False, error=err)
        
        # Prepare image
        prepared = self.prepare_image(image_data)
        if not prepared:
            return VisionResult(success=False, error="Failed to prepare image")
        
        provider_name, provider_config = self._get_vision_provider()
        if not provider_name or not provider_config:
            return VisionResult(
                success=False, 
                error="No vision-capable provider configured. Please configure a vision-capable API key (Groq, OpenRouter, Gemini, or NVIDIA)."
            )
        
        # Build prompt
        if question:
            prompt = question
            if context:
                prompt = f"{context}\n\nQuestion: {question}"
        else:
            prompt = "Describe this image in detail. Include objects, people, text, scene, and actions. Be accurate and concise."
        
        try:
            if provider_name == "groq":
                return self._analyze_groq(prepared, prompt, provider_config)
            elif provider_name == "openrouter":
                return self._analyze_openrouter(prepared, prompt, provider_config)
            elif provider_name == "gemini":
                return self._analyze_gemini(prepared, prompt, provider_config)
            elif provider_name == "nvidia":
                return self._analyze_nvidia(prepared, prompt, provider_config)
            else:
                return VisionResult(success=False, error=f"Provider {provider_name} not implemented")
        except Exception as e:
            print(f"[VISION] Analysis failed: {e}")
            return VisionResult(success=False, error=f"Vision analysis failed: {str(e)[:200]}")

    # ── Multi-image validation ─────────────────────────────────────────
    def validate_images(self, image_data_list: List[str]) -> Tuple[bool, str, List[str]]:
        """Validate multiple image data URIs before sending.

        Returns (all_valid, error_message, validated_list).
        """
        if not image_data_list or not isinstance(image_data_list, list):
            return False, "No images provided.", []

        if len(image_data_list) > self.NVIDIA_MAX_IMAGES_PER_REQUEST:
            return False, (
                f"Too many images ({len(image_data_list)}). "
                f"Maximum is {self.NVIDIA_MAX_IMAGES_PER_REQUEST} per request."
            ), []

        validated: List[str] = []
        for idx, image_data in enumerate(image_data_list, start=1):
            valid, err = self.validate_image(image_data)
            if not valid:
                return False, f"Image {idx}: {err}", []
            # Strict MIME-type check for NVIDIA pipeline.
            try:
                header = image_data.split(",", 1)[0]
                mime = header.split(";")[0].replace("data:", "").lower()
                if mime not in self.ALLOWED_MIME_TYPES and mime != "image/*":
                    return False, f"Image {idx}: unsupported image format '{mime}'.", []
            except Exception:
                return False, f"Image {idx}: malformed image URI.", []
            validated.append(image_data)

        return True, "", validated

    # ── Multi-image analysis via NVIDIA Nemotron ───────────────────────
    def analyze_images(
        self,
        image_data_list: List[str],
        question: str = "",
        context: str = "",
    ) -> VisionResult:
        """Analyze multiple images in a single Nemotron request.

        All images are sent together as image_url content parts, preserving
        ordering. The model can describe, compare, and cross-reference them.
        """
        provider_name, provider_config = self._get_vision_provider()
        if not provider_name or not provider_config:
            return VisionResult(
                success=False,
                error="No vision-capable provider configured. Please configure a vision-capable API key (Groq, OpenRouter, Gemini, or NVIDIA).",
            )

        if provider_name != "nvidia":
            return VisionResult(
                success=False,
                error=(
                    f"Multi-image analysis requires NVIDIA provider, "
                    f"but active provider is '{provider_name}'. "
                    "Configure NVIDIA_API_KEY to enable multi-image analysis."
                ),
            )

        valid, err, validated = self.validate_images(image_data_list)
        if not valid:
            return VisionResult(success=False, error=err)

        # Emit activity for image analysis.
        if len(validated) == 1:
            self._emit_activity("image_video", "examining_image", detail=f"1 image")
        else:
            self._emit_activity("image_video", "processing_images", detail=f"{len(validated)} images")

        # Build content array: images first (preserving order), then text.
        content: List[Dict[str, Any]] = []
        for image_data in validated:
            content.append({"type": "image_url", "image_url": {"url": image_data}})
        if context:
            content.append({"type": "text", "text": context})
        if question:
            content.append({"type": "text", "text": question})
        else:
            content.append({
                "type": "text",
                "text": (
                    "Analyze these images in the order provided. Describe what "
                    "you see in each image, including objects, people, scenes, "
                    "text, and actions. If multiple images show related content, "
                    "compare them and identify differences, similarities, and "
                    "changes between views. Be specific and accurate."
                ),
            })

        result = self._call_nvidia_nemotron(content, question or "", max_tokens=4096)
        result.images_analyzed = len(validated)
        if len(validated) > 1:
            self._emit_activity("image_video", "comparing_videos", detail=f"{len(validated)} images compared")
        return result

    # ── Native video analysis via NVIDIA Nemotron ──────────────────────
    def analyze_video(
        self,
        video_data_uri: str,
        question: str = "",
        context: str = "",
    ) -> VisionResult:
        """Analyze a video using Nemotron's native video input.

        Nemotron 3.5 Super VL accepts video natively via video_url — no
        frame extraction is required.
        """
        provider_config = self._get_nvidia_config()
        if not provider_config:
            return VisionResult(
                success=False,
                error="No NVIDIA vision provider configured. Please configure NVIDIA_API_KEY.",
            )

        if not video_data_uri or not isinstance(video_data_uri, str):
            return VisionResult(success=False, error="No video data provided.")

        text = video_data_uri.strip()
        if not text.lower().startswith("data:video/"):
            return VisionResult(
                success=False,
                error="Invalid video format - must be a data:video/ URI.",
            )
        if ";base64," not in text:
            return VisionResult(
                success=False,
                error="Invalid video format - missing base64 encoding.",
            )

        header, b64_data = text.split(",", 1)
        mime = header.split(";")[0].replace("data:", "").lower()
        if mime not in self.NVIDIA_ALLOWED_VIDEO_MIME_TYPES:
            return VisionResult(
                success=False, error=f"Unsupported video format: {mime}",
            )

        try:
            decoded = base64.b64decode(b64_data, validate=True)
        except Exception as exc:
            return VisionResult(
                success=False, error=f"Invalid base64 data: {str(exc)[:100]}",
            )

        if len(decoded) > self.NVIDIA_MAX_VIDEO_SIZE_BYTES:
            return VisionResult(
                success=False,
                error=(
                    f"Video too large ({len(decoded) / 1024 / 1024:.1f}MB). "
                    f"Maximum is {self.NVIDIA_MAX_VIDEO_SIZE_BYTES // 1024 // 1024}MB."
                ),
            )

        content: List[Dict[str, Any]] = [
            {"type": "video_url", "video_url": {"url": video_data_uri}},
        ]
        if context:
            content.append({"type": "text", "text": context})
        if question:
            content.append({"type": "text", "text": question})
        else:
            content.append({
                "type": "text",
                "text": (
                    "Watch this video and describe what happens. Be specific "
                    "about actions, objects, people, scenes, and notable events "
                    "over time."
                ),
            })

        result = self._call_nvidia_nemotron(content, question or "", max_tokens=8192)
        result.video_analyzed = True
        self._emit_activity("image_video", "analyzing_video", detail="Native video decoding")
        return result

    # ── Multi-video analysis via NVIDIA Nemotron ───────────────────────
    def analyze_videos(
        self,
        video_data_uri_list: List[str],
        question: str = "",
        context: str = "",
    ) -> VisionResult:
        """Analyze multiple videos through controlled sequential processing.

        Nemotron 3.5 Super VL accepts one ``video_url`` per request, so each
        video is processed individually and the findings are consolidated into
        a single structured result. Order is preserved and each observation is
        attributed to its source video so the conversational model can compare
        and cross-reference them.

        Partial failures are handled gracefully: one failed video does not
        invalidate successful analyses of the others.
        """
        provider_config = self._get_nvidia_config()
        if not provider_config:
            return VisionResult(
                success=False,
                error="No NVIDIA vision provider configured. Please configure NVIDIA_API_KEY.",
            )

        if not video_data_uri_list or not isinstance(video_data_uri_list, list):
            return VisionResult(success=False, error="No videos provided.")

        if len(video_data_uri_list) > self.NVIDIA_MAX_IMAGES_PER_REQUEST:
            return VisionResult(
                success=False,
                error=(
                    f"Too many videos ({len(video_data_uri_list)}). "
                    f"Maximum is {self.NVIDIA_MAX_IMAGES_PER_REQUEST} per request."
                ),
            )

        per_video_results: List[Dict[str, Any]] = []
        errors: List[str] = []

        # Emit activity for multi-video analysis.
        if len(video_data_uri_list) == 1:
            self._emit_activity("image_video", "analyzing_video", detail="1 video")
        else:
            self._emit_activity("image_video", "comparing_videos", detail=f"{len(video_data_uri_list)} videos")

        for idx, video_data_uri in enumerate(video_data_uri_list, start=1):
            # Validate each video before sending.
            text = (video_data_uri or "").strip()
            if not text:
                errors.append(f"Video {idx}: no video data provided.")
                per_video_results.append({
                    "index": idx,
                    "success": False,
                    "error": "No video data provided.",
                })
                continue
            if not text.lower().startswith("data:video/"):
                errors.append(f"Video {idx}: invalid format — must be data:video/ URI.")
                per_video_results.append({
                    "index": idx,
                    "success": False,
                    "error": "Invalid video format — must be data:video/ URI.",
                })
                continue
            if ";base64," not in text:
                errors.append(f"Video {idx}: missing base64 encoding.")
                per_video_results.append({
                    "index": idx,
                    "success": False,
                    "error": "Missing base64 encoding.",
                })
                continue

            header, b64_data = text.split(",", 1)
            mime = header.split(";")[0].replace("data:", "").lower()
            if mime not in self.NVIDIA_ALLOWED_VIDEO_MIME_TYPES:
                errors.append(f"Video {idx}: unsupported format '{mime}'.")
                per_video_results.append({
                    "index": idx,
                    "success": False,
                    "error": f"Unsupported video format: {mime}",
                })
                continue

            try:
                decoded = base64.b64decode(b64_data, validate=True)
            except Exception as exc:
                errors.append(f"Video {idx}: invalid base64 data.")
                per_video_results.append({
                    "index": idx,
                    "success": False,
                    "error": "Invalid base64 data.",
                })
                continue

            if len(decoded) > self.NVIDIA_MAX_VIDEO_SIZE_BYTES:
                errors.append(
                    f"Video {idx}: too large "
                    f"({len(decoded) / 1024 / 1024:.1f}MB)."
                )
                per_video_results.append({
                    "index": idx,
                    "success": False,
                    "error": "Video too large.",
                })
                continue

            # Analyze this video individually via Nemotron.
            content: List[Dict[str, Any]] = [
                {"type": "video_url", "video_url": {"url": video_data_uri}},
            ]
            vid_context = (
                f"[Video {idx} of {len(video_data_uri_list)}]\n"
                f"{context if context else ''}"
            ).strip()
            if question:
                vid_prompt = (
                    f"Analyze this video (video {idx} of {len(video_data_uri_list)}). "
                    f"{question}"
                )
            else:
                vid_prompt = (
                    f"Watch this video (video {idx} of {len(video_data_uri_list)}) "
                    f"and describe what happens. Be specific about actions, objects, "
                    f"people, scenes, and notable events over time."
                )
            content.append({"type": "text", "text": vid_prompt})

            vid_result = self._call_nvidia_nemotron(
                content, question or "", max_tokens=8192
            )

            if vid_result.success:
                per_video_results.append({
                    "index": idx,
                    "success": True,
                    "description": vid_result.description,
                })
            else:
                errors.append(f"Video {idx}: {vid_result.error}")
                per_video_results.append({
                    "index": idx,
                    "success": False,
                    "error": vid_result.error,
                })

        # Consolidate findings.
        successful = [r for r in per_video_results if r["success"]]
        failed = [r for r in per_video_results if not r["success"]]

        if not successful and not failed:
            return VisionResult(success=False, error="No videos to analyze.")

        if not successful:
            return VisionResult(
                success=False,
                error="; ".join(errors) if errors else "All video analyses failed.",
            )

        # Build consolidated description preserving per-video attribution.
        parts: List[str] = []
        if context:
            parts.append(context)
        if question:
            parts.append(f"Question: {question}")
        parts.append("")
        parts.append(
            "The following videos were analyzed sequentially through Nemotron "
            f"3.5 Super VL. Each video's findings are labeled by its upload "
            f"order (video {successful[0]['index']} of "
            f"{len(video_data_uri_list)}). "
            "Use these observations to answer the user's question, comparing "
            "and contrasting videos where relevant."
        )
        parts.append("")

        for r in per_video_results:
            if r["success"]:
                parts.append(
                    f"=== Video {r['index']} ===\n{r['description']}"
                )
            else:
                parts.append(
                    f"=== Video {r['index']} (analysis failed) ===\n"
                    f"Error: {r['error']}"
                )

        if failed:
            parts.append("")
            parts.append(
                "Note: Some videos could not be analyzed. "
                f"Successful analyses: {len(successful)}/{len(per_video_results)}. "
                f"Failed: {len(failed)}."
            )

        consolidated = "\n".join(parts)

        self._emit_activity("image_video", "combining_findings", detail=f"{len(successful)}/{len(per_video_results)} videos analyzed")

        overall_error = ""
        if failed:
            overall_error = (
                f"Partial failure: {len(failed)} of {len(per_video_results)} "
                f"video(s) could not be analyzed."
            )

        return VisionResult(
            success=True,
            description=consolidated,
            provider=f"nvidia/{self.NVIDIA_NEMOTRON_MODEL}",
            videos_analyzed=len(successful),
            video_analyzed=True,
            raw={
                "per_video_results": per_video_results,
                "successful_count": len(successful),
                "failed_count": len(failed),
                "total_count": len(per_video_results),
                "error": overall_error,
            },
        )

    # ── Combined media description for conversational pipeline ───────────
    def describe_media_for_brain(
        self,
        image_data_list: Optional[List[str]] = None,
        video_data_uri_list: Optional[List[str]] = None,
        image_result: Optional[VisionResult] = None,
        video_result: Optional[VisionResult] = None,
        question: str = "",
        context: str = "",
    ) -> VisionResult:
        """Build a single textual summary from media analysis results for the
        conversational pipeline.

        Reuses already-computed analysis results (image_result / video_result)
        so media is analyzed exactly once per request. If a pre-computed result
        is not supplied for a media type, that type is analyzed on demand
        (single-image or multi-image via analyze_images, sequential video via
        analyze_videos).

        The result preserves per-media attribution so the conversational model
        can answer questions comparing images and videos.
        """
        image_data_list = image_data_list or []
        video_data_uri_list = video_data_uri_list or []

        if not image_data_list and not video_data_uri_list:
            return VisionResult(
                success=False,
                error="No media provided for conversational analysis.",
            )

        provider_name, provider_config = self._get_vision_provider()
        if not provider_name or not provider_config:
            return VisionResult(
                success=False,
                error="No vision-capable provider configured.",
            )

        parts: List[str] = []
        if context:
            parts.append(context)
        if question:
            parts.append(f"Question: {question}")
        parts.append("")

        all_success = True

        # Use pre-computed image analysis if available; otherwise analyze once.
        if image_data_list:
            if image_result is not None:
                img_result = image_result
            elif provider_name == "nvidia":
                img_result = self.analyze_images(
                    image_data_list, question=question, context=context
                )
            else:
                # Fall back to single-image analysis for non-Nvidia providers.
                img_result = self.analyze_image(
                    image_data_list[0], question=question, context=context
                )
            if img_result.success:
                parts.append("[Image Analysis]")
                parts.append(img_result.description)
            else:
                all_success = False
                parts.append(
                    f"[Image Analysis failed: {img_result.error}]"
                )
            parts.append("")

        # Use pre-computed video analysis if available; otherwise analyze once.
        if video_data_uri_list:
            if video_result is not None:
                vid_result = video_result
            else:
                vid_result = self.analyze_videos(
                    video_data_uri_list, question=question, context=context
                )
            if vid_result.success:
                parts.append("[Video Analysis]")
                parts.append(vid_result.description)
            else:
                all_success = False
                parts.append(
                    f"[Video Analysis failed: {vid_result.error}]"
                )
            parts.append("")

        consolidated = "\n".join(parts).strip()

        self._emit_activity("image_video", "combining_findings", detail="Media findings consolidated for chat")

        return VisionResult(
            success=all_success,
            description=consolidated,
            provider=f"nvidia/{self.NVIDIA_NEMOTRON_MODEL}"
            if provider_name == "nvidia"
            else provider_name or "",
            raw=None,
        )

    # ── Internal NVIDIA Nemotron call ──────────────────────────────────
    def _call_nvidia_nemotron(
        self,
        content: List[Dict[str, Any]],
        question: str,
        max_tokens: int,
    ) -> VisionResult:
        """Send a multimodal content array to NVIDIA Nemotron 3.5 Super VL."""
        provider_config = self._get_nvidia_config()
        if not provider_config:
            return VisionResult(
                success=False,
                error="NVIDIA provider not configured. Please configure NVIDIA_API_KEY.",
            )

        api_key = provider_config["api_key"]
        base_url = provider_config.get(
            "base_url", "https://integrate.api.nvidia.com/v1"
        ).rstrip("/").removesuffix("/openai/v1")
        url = f"{base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.NVIDIA_NEMOTRON_MODEL,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": 0.6,
            "top_p": 0.95,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        import requests
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code != 200:
                return VisionResult(
                    success=False,
                    error=f"NVIDIA HTTP {resp.status_code}: {resp.text[:200]}",
                )

            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content_text = msg.get("content", "") or ""
                reasoning = msg.get("reasoning", "") or ""
                full = content_text
                if reasoning:
                    full = f"{reasoning}\n\n{content_text}"
                return VisionResult(
                    success=True,
                    description=full.strip(),
                    provider=f"nvidia/{self.NVIDIA_NEMOTRON_MODEL}",
                    raw=data,
                )

            return VisionResult(
                success=False,
                error="NVIDIA returned no response choices.",
            )

        except requests.exceptions.Timeout:
            return VisionResult(
                success=False,
                error="NVIDIA request timed out. Try with fewer or smaller attachments.",
            )
        except Exception as exc:
            return VisionResult(
                success=False,
                error=f"NVIDIA request failed: {exc}",
            )

    def _analyze_groq(self, image_data: str, prompt: str, config: Dict) -> VisionResult:
        """Analyze with Groq vision model."""
        # Try current vision models on Groq
        vision_models = [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-4-maverick-17b-128e-instruct",
            "llama-3.2-11b-vision-preview",
            "llama-3.2-90b-vision-preview",
        ]
        api_key = config["api_key"]
        base_url = config.get("base_url", "https://api.groq.com/openai/v1").rstrip('/')
        if "/openai/v1" not in base_url:
            url = f"{base_url}/openai/v1/chat/completions"
        else:
            url = f"{base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        for model in vision_models:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": image_data}}
                            ]
                        }
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.3,
                }
                resp = requests.post(url, headers=headers, json=payload, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content:
                        return VisionResult(
                            success=True,
                            description=content.strip(),
                            provider=f"groq/{model}",
                            raw=data
                        )
                elif resp.status_code in (404, 400):
                    continue  # Try next model
                else:
                    print(f"[VISION] Groq {model} failed: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                print(f"[VISION] Groq {model} error: {e}")
                continue
        
        return VisionResult(success=False, error="No compatible Groq vision model available")
    
    def _analyze_openrouter(self, image_data: str, prompt: str, config: Dict) -> VisionResult:
        """Analyze with OpenRouter vision model."""
        api_key = config["api_key"]
        base_url = config.get("base_url") or "https://openrouter.ai/api/v1"
        url = f"{base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://valleymind.ai",
            "X-Title": "ValleyMind-AI",
        }
        
        # Try common free vision models on OpenRouter
        vision_models = [
            "nvidia/nemotron-3.5-super-vl:free",
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
        ]
        
        for model in vision_models:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": image_data}}
                            ]
                        }
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.3,
                }
                resp = requests.post(url, headers=headers, json=payload, timeout=90)
                if resp.status_code == 200:
                    data = resp.json()
                    msg = data.get("choices", [{}])[0].get("message", {}) or {}
                    content = msg.get("content") or ""
                    if not content and msg.get("reasoning"):
                        content = msg.get("reasoning")
                    if not content and msg.get("reasoning_content"):
                        content = msg.get("reasoning_content")
                    if content:
                        return VisionResult(
                            success=True,
                            description=content.strip(),
                            provider=f"openrouter/{model}",
                            raw=data
                        )
                elif resp.status_code in (404, 400):
                    continue
                else:
                    print(f"[VISION] OpenRouter {model} failed: {resp.status_code} {resp.text[:150]}")
            except Exception as e:
                print(f"[VISION] OpenRouter {model} error: {e}")
                continue
        
        return VisionResult(success=False, error="No compatible OpenRouter vision model available")
    
    def _analyze_gemini(self, image_data: str, prompt: str, config: Dict) -> VisionResult:
        """Analyze with Gemini vision model."""
        api_key = config["api_key"]
        base_url = config.get("base_url") or "https://generativelanguage.googleapis.com/v1beta"
        
        # Extract base64 data
        header, b64_data = image_data.split(",", 1)
        mime = header.split(";")[0].replace("data:", "")
        
        models = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
        
        for model in models:
            try:
                url = f"{base_url}/models/{model}:generateContent?key={api_key}"
                payload = {
                    "contents": [{
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": mime, "data": b64_data}}
                        ]
                    }],
                    "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.3}
                }
                resp = requests.post(url, json=payload, timeout=90)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            content = parts[0].get("text", "")
                            if content:
                                return VisionResult(
                                    success=True,
                                    description=content.strip(),
                                    provider=f"gemini/{model}",
                                    raw=data
                                )
                elif resp.status_code in (404, 400):
                    continue
                else:
                    print(f"[VISION] Gemini {model} failed: {resp.status_code} {resp.text[:150]}")
            except Exception as e:
                print(f"[VISION] Gemini {model} error: {e}")
                continue
        
        return VisionResult(success=False, error="No compatible Gemini vision model available")
    
    def _analyze_nvidia(self, image_data: str, prompt: str, config: Dict) -> VisionResult:
        """Analyze a single image with NVIDIA Nemotron 3.5 Super VL.

        Delegates to the shared _call_nvidia_nemotron so that single-image
        and multi-image paths use the same model, endpoint, and request
        structure.
        """
        provider_name, provider_config = self._get_vision_provider()
        if not provider_name or not provider_config:
            return VisionResult(
                success=False,
                error="NVIDIA provider not configured.",
            )

        content = [
            {"type": "image_url", "image_url": {"url": image_data}},
            {"type": "text", "text": prompt},
        ]

        return self._call_nvidia_nemotron(content, prompt, max_tokens=4096)
    
    def describe_for_brain(self, image_data: str, user_question: str = "") -> str:
        """Get a grounded description suitable for passing to the brain."""
        result = self.analyze_image(image_data, user_question)
        if not result.success:
            return f"[Vision analysis unavailable: {result.error}]"
        return result.description


# Singleton instance
_vision_service = None


def get_vision_service() -> VisionService:
    global _vision_service
    if _vision_service is None:
        _vision_service = VisionService()
    return _vision_service
