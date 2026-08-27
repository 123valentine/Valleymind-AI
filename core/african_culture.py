"""African Cultural Intelligence — response language registry and cultural
grounding retrieval.

Two DISTINCT concepts, kept separate:

1. RESPONSE LANGUAGE  — the language ValleyMind should normally answer in
   (a code like "ig", "pcm", "en"). Persisted independently.

2. CULTURAL IDENTITY  — the user's chosen cultural grounding (e.g. "igbo").
   Independent from response language. Used to retrieve relevant, authentic
   cultural wisdom (proverbs/values) that may naturally enrich an answer.

This module never fabricates cultural knowledge: it only surfaces records that
were imported with provenance, and it marks unverified origin as such.

Retrieval priority (per spec):
   1. Correct cultural identity
   2. Correct language
   3. Relevant theme
   4. Verified origin
   5. Source quality
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "african_culture",
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_CAPABILITIES_FILE = os.path.join(_PROJECT_ROOT, "model_capabilities.json")

# ---------------------------------------------------------------------------
# Language ↔ code mapping (response language)
# ---------------------------------------------------------------------------
LANGUAGES = {
    "en":  {"name": "English",             "native_name": "English",                "region": "Global",     "model_support": "full"},
    "pcm": {"name": "Nigerian Pidgin",     "native_name": "Naija",                  "region": "West Africa", "model_support": "experimental"},
    "ig":  {"name": "Igbo",                "native_name": "Igbo",                   "region": "West Africa", "model_support": "experimental"},
    "yo":  {"name": "Yoruba",              "native_name": "Yoruba",                 "region": "West Africa", "model_support": "experimental"},
    "ha":  {"name": "Hausa",               "native_name": "Hausa",                  "region": "West Africa", "model_support": "experimental"},
    "sw":  {"name": "Swahili",             "native_name": "Kiswahili",              "region": "East Africa", "model_support": "good"},
    "zu":  {"name": "Zulu",                "native_name": "isiZulu",                "region": "Southern Africa", "model_support": "partial"},
    "xh":  {"name": "Xhosa",               "native_name": "isiXhosa",               "region": "Southern Africa", "model_support": "partial"},
    "af":  {"name": "Afrikaans",           "native_name": "Afrikaans",              "region": "Southern Africa", "model_support": "good"},
    "st":  {"name": "Sesotho",             "native_name": "Sesotho",                "region": "Southern Africa", "model_support": "experimental"},
    "tn":  {"name": "Setswana",            "native_name": "Setswana",               "region": "Southern Africa", "model_support": "experimental"},
    "nso": {"name": "Sepedi",              "native_name": "Sepedi",                 "region": "Southern Africa", "model_support": "experimental"},
    "nr":  {"name": "isiNdebele",          "native_name": "isiNdebele",             "region": "Southern Africa", "model_support": "experimental"},
    "ss":  {"name": "siSwati",             "native_name": "siSwati",                "region": "Southern Africa", "model_support": "experimental"},
    "ve":  {"name": "Tshivenda",           "native_name": "Tshivenda",              "region": "Southern Africa", "model_support": "experimental"},
    "ts":  {"name": "Xitsonga",            "native_name": "Xitsonga",               "region": "Southern Africa", "model_support": "experimental"},
}

# Internal resolution aliases (display names / older "reply_language" strings)
_LANGUAGE_ALIASES = {
    "nigerian pidgin": "pcm", "pidgin": "pcm", "naija": "pcm",
    "igbo": "ig", "yoruba": "yo", "hausa": "ha", "swahili": "sw",
    "kiswahili": "sw", "zulu": "zu", "isizulu": "zu", "xhosa": "xh",
    "isixhosa": "xh", "afrikaans": "af", "sesotho": "st", "setswana": "tn",
    "sepedi": "nso", "isindebele": "nr", "siswati": "ss", "tshivenda": "ve",
    "xitsonga": "ts", "english": "en", "en": "en",
}

# Cultural identity options offered in settings (value, label)
CULTURAL_IDENTITIES = [
    {"value": "none",        "label": "No specific cultural preference"},
    {"value": "igbo",        "label": "Igbo"},
    {"value": "yoruba",      "label": "Yoruba"},
    {"value": "hausa",       "label": "Hausa"},
    {"value": "nigerian",    "label": "Nigerian / broader Nigerian"},
    {"value": "akan",        "label": "Akan"},
    {"value": "swahili_ea",  "label": "Swahili / East African"},
    {"value": "zulu",        "label": "Zulu"},
    {"value": "xhosa",       "label": "Xhosa"},
    {"value": "south_african", "label": "South African / Southern African"},
    {"value": "other_african", "label": "Other African"},
    {"value": "custom",      "label": "Custom / Other"},
]

# identity -> culture keys that appear in the proverb filenames/records
_IDENTITY_CULTURE_MAP = {
    "igbo":        {"igbo"},
    "yoruba":      {"yoruba"},
    "hausa":       {"hausa"},
    "nigerian":    {"igbo", "yoruba", "hausa", "pidgin"},
    "akan":        {"akan"},
    "swahili_ea":  {"swahili"},
    "zulu":        {"zulu"},
    "xhosa":       {"xhosa"},
    "south_african": {"zulu", "xhosa", "south_african", "swahili", "pidgin"},
    "other_african": {"general_african", "amharic", "swahili", "zulu", "xhosa"},
    "custom":      None,  # any culture, prefer verified
}

# Themes that map to underlying proverb files
_THEME_KEYWORDS = {
    "patience": ["patience", "wait", "time", "slow", "hurry", "rushing"],
    "wisdom": ["wisdom", "wise", "knowledge", "learn", "know"],
    "perseverance": ["persever", "persist", "give up", "quitting", "keep going", "resilien", "endure"],
    "community": ["community", "together", "village", "collective", "neighbour", "neighbor", "support"],
    "family": ["family", "parent", "mother", "father", "child", "home"],
    "respect": ["respect", "elder", "honour", "honor", "polite"],
    "responsibility": ["responsib", "duty", "obligat", "accountab"],
    "leadership": ["leader", "king", "chief", "lead", "power"],
    "conflict": ["conflict", "fight", "quarrel", "argu", "dispute", "war"],
    "growth": ["grow", "effort", "work", "struggle", "success", "achieve"],
    "patience": ["patience"],
}

# Suitable / unsuitable contexts for offering a culturally grounded adage.
# The model must NEVER force a proverb; only offer one when it genuinely
# improves the answer and is relevant to the user's message.
_ADAGE_UNSUITABLE = [
    "2 + 2", "1 +", "+ 1", "multiply", "divide", "square root", "calculate",
    "error", "exception", "traceback", "debug", "compile", "syntax", "bug",
    "stack trace", "http", "status code", "api endpoint", "regex",
    "translate this", "translate into", "translate the following",
    "poison", "overdose", "emergency", "ambulance", "call 911", "call 999",
    "tax return", "legal advice", "medical diagnosis", "take this medication",
    "invest your savings", "buy this stock",
]

_ADAGE_SUITABLE = [
    "give up", "giving up", "hopeless", "frustrat", "stuck", "discourag",
    "relationship", "marriage", "girlfriend", "boyfriend", "breakup",
    "family", "children", "parent", "advice", "leadership", "team",
    "patience", "wait", "persever", "resilien", "responsib", "community",
    "decision", "choose", "career", "business", "goal", "disappoint",
    "conflict", "argue", "forgive", "respect", "elder", "wisdom",
]


def _load_json_any(base: str) -> Optional[dict]:
    path = os.path.join(DATA_DIR, base)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------
def resolve_language(code_or_name: str) -> Optional[str]:
    """Return the canonical language code for a code or display name, else None."""
    if not code_or_name:
        return None
    key = str(code_or_name).strip().lower()
    if key in LANGUAGES:
        return key
    alias = _LANGUAGE_ALIASES.get(key)
    if alias and alias in LANGUAGES:
        return alias
    # case-insensitive display-name match
    for code, meta in LANGUAGES.items():
        if meta["name"].lower() == key or meta["native_name"].lower() == key:
            return code
    return None


def language_label(code: str) -> str:
    meta = LANGUAGES.get(code)
    return meta["name"] if meta else (code or "English")


def _normalize(text: str) -> str:
    """Normalize whitespace, punctuation and Unicode for deduplication."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return text


# ---------------------------------------------------------------------------
# Cultural data loading
# ---------------------------------------------------------------------------
def _proverb_files() -> list[str]:
    path = os.path.join(DATA_DIR, "proverbs")
    if not os.path.isdir(path):
        return []
    return [n for n in sorted(os.listdir(path)) if n.endswith(".json")]


def _record_culture(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    if base == "general_african":
        return ""
    if base == "pidgin":
        return "pidgin"
    return base


def load_all_proverbs() -> list[dict]:
    """Load every proverb record, tagging each with its culture from the file."""
    records: list[dict] = []
    for fname in _proverb_files():
        data = _load_json_any(os.path.join("proverbs", fname).replace("\\", "/"))
        items = data.get("proverbs", data.get("items", [])) if isinstance(data, dict) else []
        culture_default = _record_culture(fname)
        for item in items:
            if not isinstance(item, dict):
                continue
            rec = dict(item)
            rec.setdefault("culture", culture_default)
            rec.setdefault("language", rec.get("language") or "")
            rec.setdefault("source_file", fname)
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Cultural RAG retrieval
# ---------------------------------------------------------------------------
def _theme_score(record: dict, themes: Iterable[str]) -> int:
    rec_themes = {str(t).lower() for t in (record.get("themes") or [])}
    kw = set()
    for t in themes or []:
        for word in _THEME_KEYWORDS.get(str(t).lower(), [t.lower()]):
            kw.add(word)
    score = 0
    for w in kw:
        if w in rec_themes:
            score += 2
    # also scan meaning/translation text for theme words
    hay = _normalize((record.get("meaning") or "") + " " + (record.get("translation_en") or ""))
    for w in kw:
        if w in hay:
            score += 1
    return score


def _message_theme_hits(message: str) -> list[str]:
    lower = (message or "").lower()
    hits = []
    for theme, words in _THEME_KEYWORDS.items():
        for w in words:
            if w in lower:
                hits.append(theme)
                break
    return hits


def retrieve_proverbs(
    cultural_identity: str = "",
    language_code: str = "",
    themes: Optional[list[str]] = None,
    message: str = "",
    limit: int = 4,
    verified_only_if_available: bool = True,
) -> list[dict]:
    """Score and return the most relevant cultural records.

    Priority: cultural identity → language → theme → verified origin → source.
    """
    records = load_all_proverbs()
    identity_key = str(cultural_identity or "").strip().lower()
    allowed_cultures = _IDENTITY_CULTURE_MAP.get(identity_key)
    themes = themes or _message_theme_hits(message)

    scored: list[dict] = []
    for rec in records:
        score = 0.0
        culture = str(rec.get("culture") or "").strip().lower()
        lang = str(rec.get("language") or "").strip().lower()

        # 1. Cultural identity
        if identity_key and identity_key != "none":
            if allowed_cultures:
                if culture in allowed_cultures:
                    score += 4.0
                elif identity_key == "custom":
                    score += 1.0  # custom: prefer verified across all
                else:
                    continue  # strong filter: only that culture's material
            else:
                score += 1.0
        else:
            # No cultural preference: don't force; rely on language/theme
            if language_code and lang == language_code:
                score += 1.0

        # 2. Language
        if language_code and lang == language_code:
            score += 2.0

        # 3. Theme relevance
        ts = _theme_score(rec, themes)
        score += ts * 1.2

        # 4. Verified origin
        verification = rec.get("verification") or {}
        origin_verified = bool(verification.get("origin_verified"))
        trans_verified = bool(verification.get("translation_verified"))
        if origin_verified:
            score += 1.0
        if trans_verified:
            score += 0.5

        # 5. Source quality (tier of the source, if any)
        source = rec.get("source") or {}
        if source.get("license"):
            score += 0.2
        if source.get("url"):
            score += 0.1

        if score <= 0:
            continue

        result = {
            "id": rec.get("id", ""),
            "culture": culture or rec.get("culture", ""),
            "language": lang or rec.get("language", ""),
            "text": rec.get("text", ""),
            "translation_en": rec.get("translation_en", ""),
            "meaning": rec.get("meaning", ""),
            "themes": rec.get("themes", []),
            "source": source,
            "origin_verified": origin_verified,
            "_score": round(score, 3),
        }
        scored.append(result)

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Adage relevance guard
# ---------------------------------------------------------------------------
def decide_adage_relevance(message: str) -> bool:
    """Heuristic gate: is this a conversation where a cultural adage could
    genuinely improve the answer (as opposed to a technical/decisional task)?
    """
    lower = str(message or "").lower().strip()

    # Never force into clearly unsuitable contexts
    for kw in _ADAGE_UNSUITABLE:
        if kw in lower:
            return False

    # Suitable advice/life contexts
    for kw in _ADAGE_SUITABLE:
        if kw in lower:
            return True
    return False


# ---------------------------------------------------------------------------
# System-prompt module builder
# ---------------------------------------------------------------------------
def format_adage_for_prompt(rec: dict) -> str:
    lines = []
    if rec.get("text"):
        lines.append(f"Proverb: {rec['text']}")
    if rec.get("translation_en"):
        lines.append(f"Translation: {rec['translation_en']}")
    if rec.get("meaning"):
        lines.append(f"Meaning: {rec['meaning']}")
    if rec.get("origin_verified") is False:
        lines.append("NOTE: origin NOT yet independently verified.")
    return "\n".join(lines)


def build_cultural_grounding_block(
    response_language: str = "en",
    cultural_identity: str = "",
    use_adages: bool = True,
    retrieved: Optional[list[dict]] = None,
    message: str = "",
) -> str:
    """Compile the language + cultural-grounding system-prompt section.

    Returns an empty string if there is nothing meaningful to inject (so callers
    can skip it without changing existing behaviour).
    """
    blocks: list[str] = []

    # 1. Response language (always injected when a non-English language is set)
    lang_code = resolve_language(response_language) or "en"
    if lang_code and lang_code != "en":
        name = language_label(lang_code)
        extra = ""
        if lang_code == "pcm":
            extra = (
                " Use natural, everyday Nigerian Pidgin (Naija) as people actually "
                "speak it — NOT literal word-for-word translation. Nigerian Pidgin "
                "is a legitimate language variety: do not 'correct' it into Standard "
                "English unless the user explicitly asks for translation/correction."
            )
        elif lang_code == "ig":
            extra = " Write in natural, fluent Igbo."
        blocks.append(
            f"RESPONSE LANGUAGE: Reply ENTIRELY in {name} no matter what language the "
            f"user writes in.{extra} If you truly cannot produce fluent {name}, reply in "
            f"English instead — never mix the two mid-reply, do not apologise about "
            f"language. The user may temporarily ask you to translate into another "
            f"language; honour that request, but do not permanently change this setting."
        )

    # 2. Cultural identity (independent from language)
    identity = str(cultural_identity or "").strip().lower()
    if identity and identity != "none":
        identity_name = identity.replace("_", " ").title()
        if identity == "swahili_ea":
            identity_name = "Swahili / East African"
        elif identity == "south_african":
            identity_name = "South African / Southern African"
        elif identity == "other_african":
            identity_name = "African (general)"
        blocks.append(
            f"USER CULTURAL IDENTITY: {identity_name}. This is the user's chosen cultural "
            f"grounding — it is INDEPENDENT of the response language. When it is genuinely "
            f"appropriate (life advice, relationships, family, leadership, patience, "
            f"resilience, responsibility, community, personal growth, conflict, decision-"
            f"making, respect), you may naturally weave in relevant cultural wisdom, "
            f"expressions, values or proverbs from this tradition. Never force a proverb. "
            f"Never assume this identity describes every member of a group. Never present "
            f"cultural wisdom as scientific, medical, legal or financial evidence."
        )

    # 3. Retrieved cultural context (only when adages are enabled and relevant)
    relevant = use_adages and decide_adage_relevance(message)
    if relevant and retrieved:
        records_text = []
        for i, rec in enumerate(retrieved, 1):
            records_text.append(f"{i}. {format_adage_for_prompt(rec)}")
        blocks.append(
            "RETRIEVED CULTURAL CONTEXT (use these authentic records rather than inventing "
            "proverbs):\n" + "\n".join(records_text) +
            "\nOnly draw on these when they genuinely enrich the answer. Do NOT insert a "
            "proverb into every reply. Do not relay any record whose 'origin NOT yet "
            "independently verified' note appears as if it were a confirmed cultural fact."
        )

    if not blocks:
        return ""
    return "\n\n".join(blocks)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_model_capabilities() -> dict:
    """Load the model/language capability layer (model_capabilities.json)."""
    try:
        with open(MODEL_CAPABILITIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"languages": {}}


def model_support_for(code: str) -> str:
    caps = load_model_capabilities().get("languages", {})
    entry = caps.get(code) or {}
    if entry.get("model_support"):
        return entry["model_support"]
    meta = LANGUAGES.get(code)
    return meta.get("model_support", "experimental") if meta else "experimental"


def supported_languages() -> list[dict]:
    """Return the full selector list (from supported_languages.json if it exists,
    otherwise derived from the built-in registry)."""
    data = _load_json_any(os.path.join("languages", "supported_languages.json"))
    items = data.get("languages") if isinstance(data, dict) else None
    if not items:
        items = []
        for code, meta in LANGUAGES.items():
            items.append({
                "code": code,
                "name": meta["name"],
                "native_name": meta["native_name"],
                "region": meta["region"],
                "model_support": meta["model_support"],
                "cultural_data_available": code in ("ig", "yo", "ha", "sw", "zu", "xh", "pcm", "af", "st", "tn", "nso", "nr", "ss", "ve", "ts", "en"),
            })
    return items
