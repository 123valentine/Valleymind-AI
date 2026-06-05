import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Load once from the project root. Environment variables already present in the
# host process keep priority unless override=True is passed by a caller.
load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class APIConfig:
    api_key: str
    news_api_key: str
    news_api_1: str
    news_api_2: str
    sports_api_key: str
    groq_api_key: str
    groq_model: str
    groq_base_url: str
    newscatcher_api_key: str
    currents_api_key: str
    api_sports_key: str
    mongodb_uri: str
    openrouter_api_key: str
    openrouter_base_url: str
    openrouter_model: str
    nvidia_api_key: str
    nvidia_base_url: str
    nvidia_model: str
    gemini_api_key: str
    gemini_base_url: str
    gemini_model: str



def get_config() -> APIConfig:
    return APIConfig(
        api_key=os.getenv("API_KEY", "").strip(),
        news_api_key=os.getenv("NEWS_API_KEY", "").strip(),
        news_api_1=os.getenv("NEWS_API_1", "").strip(),
        news_api_2=os.getenv("NEWS_API_2", "").strip(),
        sports_api_key=os.getenv("SPORTS_API_KEY", "").strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "").strip(),
        groq_base_url=os.getenv(
            "GROQ_BASE_URL",
            "https://api.groq.com",
        ).rstrip("/").removesuffix("/openai/v1"),
        newscatcher_api_key=os.getenv("NEWSCATCHER_API_KEY", "").strip(),
        currents_api_key=os.getenv("CURRENTS_API_KEY", "").strip(),
        api_sports_key=os.getenv("API_SPORTS_KEY", "").strip(),
        mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        openrouter_base_url=os.getenv(
            "OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1",
        ).rstrip("/"),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip(),
        nvidia_api_key=os.getenv("NVIDIA_API_KEY", "").strip(),
        nvidia_base_url=os.getenv(
            "NVIDIA_BASE_URL",
            "https://integrate.api.nvidia.com/v1",
        ).rstrip("/"),
        nvidia_model=os.getenv("NVIDIA_MODEL", "nvidia/llama-3.1-nv-8b-instruct").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_base_url=os.getenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        ).rstrip("/"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip(),
    )


def require_groq_key() -> str:
    key = get_config().groq_api_key
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    return key
