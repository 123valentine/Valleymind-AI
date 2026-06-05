import asyncio
import re
import time
from pathlib import Path

from core.config import PROJECT_ROOT


TTS_FOLDER = PROJECT_ROOT / "memory_data" / "tts"


def _safe_filename() -> str:
    return f"marcus_{int(time.time() * 1000)}.mp3"


def _clean_text(text: str) -> str:
    text = re.sub(r"`{1,3}.*?`{1,3}", " ", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def _edge_to_file(text: str, output_path: Path):
    import edge_tts

    communicate = edge_tts.Communicate(text, "en-US-GuyNeural")
    await communicate.save(str(output_path))


def speak_marcus(text: str) -> dict:
    """
    Optional Marcus-only local TTS.

    Generate Marcus speech through the existing local TTS hook.

    Edge TTS is preferred because it produces a browser-playable audio file.
    If it is unavailable or fails, the frontend can fall back to Chrome/browser
    speech synthesis using the returned reason.
    """
    text = _clean_text(text)
    if not text:
        return {"enabled": False, "spoken": False, "reason": "empty text"}

    try:
        TTS_FOLDER.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename()
        output_path = TTS_FOLDER / filename
        asyncio.run(asyncio.wait_for(_edge_to_file(text[:1600], output_path), timeout=15))
        if output_path.exists() and output_path.stat().st_size > 0:
            print(f"[TTS] Edge TTS generated: {filename}")
            return {
                "enabled": True,
                "spoken": False,
                "engine": "edge_tts",
                "url": f"/tts/{filename}",
            }
        raise RuntimeError("Edge TTS did not create an audio file")
    except Exception as exc:
        print(f"[WARNING] Edge TTS failed, browser speech fallback required: {exc}")

    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        print("[TTS] pyttsx3 spoke locally")
        return {"enabled": True, "spoken": True, "engine": "pyttsx3"}
    except ImportError:
        return {
            "enabled": True,
            "spoken": False,
            "engine": "browser",
            "reason": "edge_tts failed and pyttsx3 is not installed",
        }
    except Exception as exc:
        print(f"[WARNING] Marcus local TTS failed, browser speech fallback required: {exc}")
        return {"enabled": True, "spoken": False, "engine": "browser", "reason": str(exc)}
