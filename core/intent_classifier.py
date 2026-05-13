import re


I0_CONVERSATION = "I0_CONVERSATION"
I1_FACT = "I1_FACT"
I2_PROBLEM = "I2_PROBLEM"
I3_CREATIVE = "I3_CREATIVE"
I4_SUPPORT = "I4_SUPPORT"
I5_STRATEGY = "I5_STRATEGY"
I6_NEWS = "I6_NEWS"
I7_SPORTS = "I7_SPORTS"


SUPPORT_PATTERNS = [
    r"\b(i feel|i am feeling|tired|sad|angry|depressed|frustrated|scared|lonely)\b",
]

FACT_PATTERNS = [
    r"\b(what is|who is|when did|define|meaning of|explain)\b",
    r"^\s*(physics|quantum physics|chemistry|biology|mathematics|math|history|geography|programming|computer science|machine learning|artificial intelligence)\s*[?.!]*\s*$",
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

CONVERSATION_PATTERNS = [
    r"^\s*(hi|hii+|hello|hey|heyy+|yo|sup|good morning|good afternoon|good evening)[!. ]*\s*$",
    r"\b(who are you|what are you|what is your name|what's your name|your name|what can you do|how can you help)\b",
    r"\b(what do you remember|what do you know about me|what have i told you|do you remember|what is my name|what's my name)\b",
]

NEWS_PATTERNS = [
    r"\b(news|headline|headlines|breaking|latest|current events|today's news|world news|bbc news|market news|tech news|ai news)\b",
    r"\b(latest|current|recent|today)\b.*\b(news|update|updates|headline|headlines)\b",
]

SPORTS_PATTERNS = [
    r"\b(sports|score|scores|fixture|fixtures|match|matches|standings|nba|nfl|mlb|nhl|epl|premier league|champions league|football|soccer|liverpool|arsenal|chelsea|manchester city|man united)\b",
]


def _match(patterns, text):
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def classify(user_input: str) -> dict:
    text = str(user_input or "").strip()

    if _match(SPORTS_PATTERNS, text):
        return {"intent": I7_SPORTS, "confidence": 0.95}
    if _match(NEWS_PATTERNS, text):
        return {"intent": I6_NEWS, "confidence": 0.95}
    if _match(CONVERSATION_PATTERNS, text):
        return {"intent": I0_CONVERSATION, "confidence": 0.95}
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

    return {"intent": I0_CONVERSATION, "confidence": 0.55}
