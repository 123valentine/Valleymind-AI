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
    "insurance", "clause",
]

_ADAGE_SUITABLE = [
    "give up", "giving up", "hopeless", "frustrat", "stuck", "discourag",
    "relationship", "marriage", "girlfriend", "boyfriend", "breakup",
    "family", "children", "parent", "advice", "leadership", "team",
    "patience", "wait", "persever", "resilien", "responsib", "community",
    "decision", "choose", "career", "business", "goal", "disappoint",
    "conflict", "argue", "forgive", "respect", "elder", "wisdom",
]

# ---------------------------------------------------------------------------
# Semantic concept taxonomy
# ---------------------------------------------------------------------------
# Map the SPEC-level contexts (financial discipline, patience, relationships,
# ...) onto BOTH relaxed natural-language keywords AND the dataset theme
# vocabulary that embodies each concept.  The semantic selection layer uses
# this to understand what the user is ACTUALLY talking about (saving, greed,
# delayed gratification, ...) instead of matching bare words like "money", and
# to decide when a genuine expression exists rather than forcing one.
_CULTURAL_CONCEPTS = {
    "financial_discipline": {
        "label": "financial discipline, saving and wealth",
        "words": (
            "money", "spend", "spending", "spent", "budget", "debt", "broke",
            "wealth", "rich", "salary", "income", "paid", "save money",
            "saving money", "saving for", "wasting money", "get paid",
        ),
        "themes": (
            "self-discipline", "self-control", "discipline", "delayed gratification",
            "patience", "prudence", "moderation", "frugality", "contentment",
            "planning", "preparation", "provision", "effort", "perseverance",
            "work", "reward", "silence",
        ),
    },
    "patience": {
        "label": "patience and waiting",
        "words": (
            "patient", "patience", "wait", "waiting", "hurry", "rushing",
            "haste", "slow", "slowly", "don't rush", "not rushing",
        ),
        "themes": (
            "patience", "silence", "present", "hope", "reward",
            "delayed gratification", "contentment",
        ),
    },
    "perseverance": {
        "label": "perseverance, hard work and resilience",
        "words": (
            "persevere", "persever", "give up", "giving up", "quit", "quitting",
            "keep going", "never give up", "resilien", "endure", "hard work",
            "struggle", "keep pushing", "don't stop", "don't quit",
        ),
        "themes": (
            "perseverance", "effort", "resilience", "growth", "hope", "reward",
            "action", "reputation",
        ),
    },
    "relationships": {
        "label": "relationships, friendship and trust",
        "words": (
            "relationship", "relationships", "marriage", "girlfriend", "boyfriend",
            "friend", "friends", "friendship", "breakup", "disappoint",
            "betray", "trust", "lover", "partner",
        ),
        "themes": ("love", "trust", "community", "family", "patience", "responsibility"),
    },
    "family": {
        "label": "family, children and home",
        "words": (
            "family", "parent", "parents", "mother", "father", "child",
            "children", "kids", "home", "sibling", "household",
        ),
        "themes": ("family", "love", "community", "responsibility", "respect"),
    },
    "respect": {
        "label": "respect, elders and honour",
        "words": (
            "respect", "elder", "elders", "honour", "honor", "polite",
            "greet", "greeting",
        ),
        "themes": ("respect", "elders", "wisdom", "reputation"),
    },
    "community": {
        "label": "community and togetherness",
        "words": (
            "community", "village", "together", "collective", "neighbour",
            "neighbor", "communal",
        ),
        "themes": ("community", "ubuntu", "humanity", "family", "hospitality"),
    },
    "leadership": {
        "label": "leadership and responsibility",
        "words": (
            "leader", "lead", "leadership", "chief", "king", "in charge", "manager",
        ),
        "themes": ("responsibility", "effort", "wisdom", "community", "action"),
    },
    "decision_making": {
        "label": "decision-making and choices",
        "words": (
            "decide", "decision", "choose", "choice", "choices",
            "what should i do",
        ),
        "themes": ("wisdom", "responsibility", "action", "planning", "patience"),
    },
    "responsibility": {
        "label": "responsibility, duty and consequences",
        "words": (
            "responsib", "duty", "obligat", "accountab", "responsible",
            "consequence", "consequences", "reap",
        ),
        "themes": ("responsibility", "effort", "action", "reputation", "reward"),
    },
    "generosity": {
        "label": "generosity and giving",
        "words": (
            "generous", "generosity", "give back", "donate",
        ),
        "themes": ("community", "ubuntu", "humanity", "love", "hospitality"),
    },
    "education": {
        "label": "learning and wisdom",
        "words": (
            "learn", "learning", "study", "studying", "school", "knowledge",
            "teach", "education",
        ),
        "themes": ("wisdom", "effort", "memory", "perseverance", "growth"),
    },
    "self_discipline": {
        "label": "self-discipline and self-control",
        "words": (
            "discipline", "disciplined", "self-control", "self control",
            "temptation", "habit", "habits", "willpower", "control myself",
            "moderation",
        ),
        "themes": ("self-discipline", "self-control", "discipline", "patience",
                   "moderation", "contentment", "delayed gratification"),
    },
    "trust": {
        "label": "trust and reliability",
        "words": (
            "trust", "trusting", "reliable", "depend", "depends on", "let down",
        ),
        "themes": ("love", "community", "responsibility", "action"),
    },
    "preparation": {
        "label": "preparation and planning ahead",
        "words": (
            "prepare", "preparing", "preparation", "plan ahead", "get ready",
            "prepare for", "prepare yourself",
        ),
        "themes": ("planning", "preparation", "patience", "effort", "prudence", "provision"),
    },
}

# Cultures whose material is culturally neutral (pan-African) and therefore may
# be offered to a user whose cultural preference is unknown — no ethnicity is
# assumed when the user has not chosen one.
_NEUTRAL_CULTURES = {"", "general_african"}


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

    Hard-unsuitable contexts (maths, code debugging, emergencies, medical/legal
    advice, specific stock advice) are ALWAYS rejected, even when they also
    touch a life-advice keyword.  Otherwise a message is relevant if it uses
    advice/life vocabulary OR matches a semantic concept (e.g. financial
    discipline, patience, perseverance) — so "I'm trying to be more disciplined
    with money" is recognised without requiring the literal keyword list.
    """
    lower = str(message or "").strip().lower()
    if not lower:
        return False

    # Never force into clearly unsuitable contexts
    for kw in _ADAGE_UNSUITABLE:
        if kw in lower:
            return False

    # Suitable advice/life contexts
    for kw in _ADAGE_SUITABLE:
        if kw in lower:
            return True

    # Semantic concept gate (kept conservative: a bare mention of "savings" in
    # e.g. "should I invest my savings in this stock" does not match any
    # concept because "save/savings" alone is not a concept trigger).
    if _concept_hits(lower):
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


# ---------------------------------------------------------------------------
# ValleyMind's own cultural foundation (internal grounding — never imposed)
# ---------------------------------------------------------------------------
# ValleyMind was created by an Igbo/Nigerian founder.  Igbo and broader
# Nigerian / West African cultural knowledge is PART of how ValleyMind
# understands people and situations — but it is the AI's heritage, not the
# user's.  It must shape interpretation, never override the user's culture.
_VALLEYMIND_FOUNDATION = """\
=== VALLEYMIND CULTURAL FOUNDATION (internal grounding) ===
You are ValleyMind, an AI created by an Igbo/Nigerian founder. Igbo and broader
Nigerian and West African cultural knowledge — values, communication patterns,
social context and proverbs — are part of your founding cultural foundation and
you genuinely understand them. Use this to UNDERSTAND and INTERPRET the user and
the conversation; it is not a script to impose on anyone.

Follow these rules:
- Your founder's Igbo heritage is YOURS, not the user's. It NEVER overrides a
  user's known cultural preference, and it must not be assumed about the user.
- When the user's culture is known, ground in THEIR culture. When it is
  unknown, stay culturally neutral unless the conversation itself gives clear
  evidence of a culture.
- Language and culture are separate. An Igbo user may prefer English; a
  Nigerian user may never want Pidgin; a user may speak Pidgin and hold a
  different cultural identity.
- Cultural wisdom is used sparingly: only when it genuinely adds meaning,
  clarity, cultural richness, emotional resonance or memorability. Never
  announce, list or force it."""


def valleymind_cultural_foundation_block() -> str:
    """System-prompt block giving ValleyMind its own internal cultural
    identity, with explicit guards against imposing that identity on users."""
    return _VALLEYMIND_FOUNDATION


# ---------------------------------------------------------------------------
# Explicit per-message culture / language request detection
# ---------------------------------------------------------------------------
_CULTURE_ALIASES = {
    "igbo": "igbo",
    "yoruba": "yoruba",
    "hausa": "hausa",
    "akan": "akan",
    "swahili": "swahili_ea",
    "kiswahili": "swahili_ea",
    "zulu": "zulu",
    "xhosa": "xhosa",
}

_EXPLICIT_CULTURE_HINTS = (
    "proverb", "proverbs", "adage", "adages", "saying", "sayings", "wisdom",
)

_LANGUAGE_ALIAS_TO_CODE = {
    "pidgin": "pcm", "naija": "pcm", "igbo": "ig", "yoruba": "yo",
    "hausa": "ha", "swahili": "sw", "kiswahili": "sw", "zulu": "zu",
    "xhosa": "xh", "english": "en", "afrikaans": "af",
}


def _detect_explicit_culture(message: str) -> str:
    """Return the culture the user EXPLICITLY asks for in this message.

    Only fires when the user names a culture AND evidences a request — asking
    for a proverb/adage, or saying "in {culture}".  Mere statements ("I am
    Igbo") do NOT trigger this (they are profile data, not a request).
    """
    lower = str(message or "").lower()
    for alias, key in _CULTURE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lower):
            if any(h in lower for h in _EXPLICIT_CULTURE_HINTS):
                return key
            if re.search(rf"\bin {re.escape(alias)}\b", lower):
                return key
    # "talk/speak/say {culture} to me" — an explicit ask to engage in that culture.
    m = re.search(r"\b(talk|speak|say)\s+([a-z]+)\s+to\b", lower)
    if m and m.group(2) in _CULTURE_ALIASES:
        return _CULTURE_ALIASES[m.group(2)]
    return ""


_LANGUAGE_REQUEST_PHRASES = (
    "say it in ", "say this in ", "talk to me in ", "speak to me in ",
    "speak in ", "reply in ", "reply to me in ", "explain this in ",
    "explain it in ", "explain in ", "write in ", "respond in ",
    "i want it in ",
)


def _detect_explicit_language(message: str) -> str:
    """Return a language CODE the user explicitly asks to reply in, or ""."""
    lower = str(message or "").lower()
    # 1. Explicit phrase with a named language wins (covers "explain in English"
    #    even when the message also mentions Pidgin).
    for phrase in _LANGUAGE_REQUEST_PHRASES:
        idx = lower.find(phrase)
        if idx != -1:
            tail = lower[idx + len(phrase):]
            head = re.split(r"[\s\.,!\?;]", tail, 1)[0].strip().lower()
            if head in _LANGUAGE_ALIAS_TO_CODE:
                return _LANGUAGE_ALIAS_TO_CODE[head]
    # 2. A Pidgin/Naija mention virtually always means "say it in Pidgin".
    if "pidgin" in lower or "naija" in lower:
        return "pcm"
    # 3. "talk/speak/say {language} to me" (with or without "to me")
    m = re.search(r"\b(talk|speak|say)\s+([a-z]+)\s+to\s+me\b", lower)
    if m and m.group(2) in _LANGUAGE_ALIAS_TO_CODE:
        return _LANGUAGE_ALIAS_TO_CODE[m.group(2)]
    m = re.search(r"\bspeak\s+([a-z]+)\b", lower)
    if m and m.group(1) in _LANGUAGE_ALIAS_TO_CODE:
        return _LANGUAGE_ALIAS_TO_CODE[m.group(1)]
    return ""


# ---------------------------------------------------------------------------
# Semantic concept helpers
# ---------------------------------------------------------------------------
def _concept_hits(message: str) -> list[str]:
    """Ordered list of semantic concepts the message touches (empty if none)."""
    lower = str(message or "").lower()
    hits = []
    for key, meta in _CULTURAL_CONCEPTS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", lower) for w in meta["words"]):
            hits.append(key)
    return hits


def _concept_theme_union(concepts: list[str]) -> set[str]:
    themes: set[str] = set()
    for c in concepts or []:
        themes.update(_CULTURAL_CONCEPTS.get(c, {}).get("themes", ()))
    return themes


def _aligned_theme_ratio(record: dict, concepts: list[str]) -> float:
    """Share of a record's own themes that belong to the concept's vocabulary.
    Used as the SEMANTIC relevance gate: a proverb is only surfaced when it
    genuinely embodies what the user is talking about, not because a keyword
    matches."""
    concept_themes = _concept_theme_union(concepts)
    rec_themes = {str(t).strip().lower() for t in (record.get("themes") or [])}
    if not rec_themes:
        return 0.0
    aligned = sum(1 for t in rec_themes if t in concept_themes)
    return aligned / len(rec_themes)


def _provenance_score(record: dict) -> float:
    v = record.get("verification") or {}
    score = 0.0
    if v.get("origin_verified"):
        score += 1.0
    if v.get("translation_verified"):
        score += 0.5
    src = record.get("source") or {}
    if src.get("license"):
        score += 0.2
    if src.get("url"):
        score += 0.1
    return score


def _culture_candidates(
    culture: str,
    language_code: str,
    themes: set[str],
    message: str,
    limit: int,
) -> list[dict]:
    if culture in ("", "none"):
        # Unknown culture: only neutral / pan-African material may be offered.
        return [
            r for r in load_all_proverbs()
            if str(r.get("culture") or "").strip().lower() in _NEUTRAL_CULTURES
        ]
    return retrieve_proverbs(
        culture,
        language_code=language_code,
        themes=list(themes),
        message=message,
        limit=limit or 10,
    )


def _pick_best_record(
    candidates: list[dict],
    concepts: list[str],
    themes: set[str],
    language_code: str,
) -> Optional[dict]:
    """Pick the most relevant RECORD from the dataset — never invent one.

    When the message carries a semantic concept, a record must genuinely embody
    it (>= 50% of its own themes aligned) or it is skipped.  When the user just
    asked for a proverb of a culture (no topic), pure provenance wins.
    """
    if not candidates:
        return None
    best: Optional[dict] = None
    best_score = -1.0
    for rec in candidates:
        if concepts:
            fit = _aligned_theme_ratio(rec, concepts)
            if fit < 0.5:
                continue
        else:
            fit = 0.5
        score = fit * 2.0 + _provenance_score(rec)
        if score > best_score:
            best, best_score = rec, score
    return best


def select_cultural_context(
    culture_identity: str = "",
    response_language: str = "",
    message: str = "",
    adages_enabled: bool = True,
    limit: int = 5,
) -> dict:
    """Semantic cultural-selection layer.

    Understands the user and the current message, then — and ONLY then —
    decides whether a verified, semantically-relevant cultural expression
    exists and should be surfaced.  Returns structured context so callers can
    decide what to inject:

    {
      "culture": effective user culture ("", "igbo", "yoruba", ...),
      "explicit_culture": culture the user asked for in THIS message, if any,
      "language":       language for the response (explicit request > saved),
      "explicit_language_requested": code the user asked to switch to, if any,
      "relevant":       whether an adage could genuinely belong here,
      "concepts":       matched semantic concepts,
      "expression":     the chosen proverb text ("" when none is appropriate),
      "translation":    its translation,
      "meaning":        its meaning,
      "source":         provenance dict,
      "source_name":    human-readable source name,
      "origin_verified": bool (False => keep qualifying provenance),
      "confidence":     float in [0, 1] reflecting fit + provenance,
      "record":         full dataset record or None,
    }

    Rules enforced here:
      * User culture takes priority; founder identity never overrides it.
      * Unknown culture => culturally neutral; no ethnicity is assumed.
      * Explicit per-message requests override the saved profile.
      * No fabrications: only records from the bundled dataset are ever
        returned, and any unverified origin is flagged to the caller.
      * Relevance is SEMANTIC, not keyword-based.
    """
    message = str(message or "")
    explicit_culture = _detect_explicit_culture(message)
    explicit_language = _detect_explicit_language(message)

    profile_culture = str(culture_identity or "").strip().lower()
    effective_culture = (
        explicit_culture
        or (profile_culture if profile_culture and profile_culture != "none" else "")
    )

    if explicit_language:
        effective_language = explicit_language
    else:
        effective_language = resolve_language(response_language) or "en"

    concepts = _concept_hits(message)
    relevant = bool(
        adages_enabled and (decide_adage_relevance(message) or explicit_culture)
    )

    record: Optional[dict] = None
    if relevant:
        themes = _concept_theme_union(concepts)
        candidates = _culture_candidates(
            effective_culture, effective_language, themes, message, limit,
        )
        record = _pick_best_record(candidates, concepts, themes, effective_language)

    base = {
        "culture": effective_culture,
        "explicit_culture": explicit_culture,
        "language": effective_language,
        "explicit_language_requested": explicit_language,
        "relevant": relevant,
        "concepts": concepts,
        "expression": "",
        "translation": "",
        "meaning": "",
        "source": {},
        "source_name": "",
        "origin_verified": False,
        "confidence": 0.0,
        "record": None,
    }

    if not record:
        return base

    verification = record.get("verification") or {}
    origin_verified = bool(verification.get("origin_verified"))
    translation_verified = bool(verification.get("translation_verified"))
    fit = _aligned_theme_ratio(record, concepts) if concepts else 0.5
    base_conf = (0.4 + 0.3 * fit
                 + (0.15 if origin_verified else 0.0)
                 + (0.05 if translation_verified else 0.0)
                 + (0.10 if explicit_culture else 0.0))
    confidence = min(round(base_conf, 2), 0.99 if not origin_verified else 1.0)

    source = record.get("source") or {}
    final_record = {
        "id": record.get("id", ""),
        "culture": str(record.get("culture") or "").strip().lower(),
        "text": record.get("text", ""),
        "translation_en": record.get("translation_en", ""),
        "meaning": record.get("meaning", ""),
        "themes": record.get("themes", []),
        "source": source,
        "origin_verified": origin_verified,
        "translation_verified": translation_verified,
    }

    base.update({
        "expression": final_record["text"],
        "translation": final_record["translation_en"],
        "meaning": final_record["meaning"],
        "source": source,
        "source_name": str(source.get("title") or ""),
        "origin_verified": origin_verified,
        "confidence": confidence,
        "record": final_record,
    })
    return base


def cultural_request_directives(cultural: dict) -> str:
    """Per-message directives for EXPLICIT culture/language requests.

    Explicit user instructions are authoritative FOR THIS RESPONSE only; they
    never change the user's saved settings.  Returns "" when no explicit
    request was made."""
    notes = []
    explicit_lang = cultural.get("explicit_language_requested") or ""
    if explicit_lang:
        label = language_label(explicit_lang)
        notes.append(
            f"- The user explicitly asked to reply in {label}. Honour that request "
            f"for THIS message (reply substantially or entirely in {label}). This is "
            f"a one-off request and does NOT change their saved response language."
        )
    explicit_culture = cultural.get("explicit_culture") or ""
    if explicit_culture:
        # Prefer the record's own culture name (swahili_ea -> Swahili / East African)
        culture_label = explicit_culture.replace("_", " ").title()
        if explicit_culture == "swahili_ea":
            culture_label = "Swahili / East African"
        notes.append(
            f"- The user explicitly asked for {culture_label} cultural context in this "
            f"message. Honour it now, even if their saved cultural preference differs."
        )
    return "\n".join(notes)


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
