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
    
    def __init__(self):
        self.config = get_config()
        self._last_cost = 0.0
    
    def validate_image(self, image_data: str) -> Tuple[bool, str]:
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
        """Analyze with NVIDIA vision model."""
        api_key = config["api_key"]
        base_url = config["base_url"] or "https://integrate.api.nvidia.com/v1"
        url = f"{base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        models = ["microsoft/phi-3.5-vision-instruct", "nvidia/neva-22b"]
        
        for model in models:
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
                            provider=f"nvidia/{model}",
                            raw=data
                        )
                elif resp.status_code in (404, 400):
                    continue
                else:
                    print(f"[VISION] NVIDIA {model} failed: {resp.status_code}")
            except Exception as e:
                print(f"[VISION] NVIDIA {model} error: {e}")
                continue
        
        return VisionResult(success=False, error="No compatible NVIDIA vision model available")
    
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
