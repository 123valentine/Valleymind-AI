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
    sports_api_key: str
    groq_api_key: str
    groq_model: str
    groq_base_url: str
    newscatcher_api_key: str
    currents_api_key: str
    api_sports_key: str
    mongodb_uri: str


def get_config() -> APIConfig:
    return APIConfig(
        api_key=os.getenv("API_KEY", "").strip(),
        news_api_key=os.getenv("NEWS_API_KEY", "").strip(),
        sports_api_key=os.getenv("SPORTS_API_KEY", "").strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "").strip(),
        groq_base_url=os.getenv(
            "GROQ_BASE_URL",
            "https://api.groq.com/openai/v1",
        ).rstrip("/"),
        newscatcher_api_key=os.getenv("NEWSCATCHER_API_KEY", "").strip(),
        currents_api_key=os.getenv("CURRENTS_API_KEY", "").strip(),
        api_sports_key=os.getenv("API_SPORTS_KEY", "").strip(),
        mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
    )


def require_groq_key() -> str:
    key = get_config().groq_api_key
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    return key
