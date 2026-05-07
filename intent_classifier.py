
# intent_classifier.py
# Marcus Intent Classifier – v1

import re

# Intent labels
I1_FACT = "I1_FACT"
I2_PROBLEM = "I2_PROBLEM"
I3_CREATIVE = "I3_CREATIVE"
I4_SUPPORT = "I4_SUPPORT"
I5_STRATEGY = "I5_STRATEGY"


SUPPORT_PATTERNS = [
    r"\b(i feel|i am feeling|tired|sad|angry|depressed|frustrated|scared|lonely)\b",
]

FACT_PATTERNS = [
    r"\b(what is|who is|when did|define|meaning of|explain)\b",
]

PROBLEM_PATTERNS = [
    r"\b(how do i|how can i|error|not working|fix|issue|bug|failed)\b",
]

CREATIVE_PATTERNS = [
    r"\b(write|generate|create|design|story|poem|lyrics)\b",
]

STRATEGY_PATTERNS = [
    r"\b(should i|best way|long term|plan|strategy|compare|decide)\b",
]


def _match(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def classify(user_input: str) -> dict:
    text = user_input.strip()

    if _match(SUPPORT_PATTERNS, text):
        return {"intent": I4_SUPPORT, "confidence": 0.9}

    if _match(STRATEGY_PATTERNS, text):
        return {"intent": I5_STRATEGY, "confidence": 0.85}

    if _match(PROBLEM_PATTERNS, text):
        return {"intent": I2_PROBLEM, "confidence": 0.85}

    if _match(CREATIVE_PATTERNS, text):
        return {"intent": I3_CREATIVE, "confidence": 0.8}

    if _match(FACT_PATTERNS, text):
        return {"intent": I1_FACT, "confidence": 0.8}

    # Default fallback
    return {"intent": I2_PROBLEM, "confidence": 0.6}