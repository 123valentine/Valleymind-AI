from core.config import get_config


FALLBACK_GROQ_MODEL = "llama-3.3-70b-versatile"


def get_latest_groq_model(api_key: str = "") -> str:
    """
    Resolve the configured/best conversational Groq model.

    Priority:
    1. GROQ_MODEL from .env/environment
    2. Conservative chat-model fallback
    """
    config = get_config()
    if config.groq_model:
        return config.groq_model

    print(f"[API] GROQ_MODEL is empty. Using stable fallback model: {FALLBACK_GROQ_MODEL}")
    return FALLBACK_GROQ_MODEL
