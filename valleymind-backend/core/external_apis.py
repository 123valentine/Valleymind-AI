import json
import os
import re
from datetime import datetime
import requests
from groq import Groq

from core.auto_model import get_latest_groq_model
from core.config import get_config


LIVE_DATA_UNAVAILABLE = "__LIVE_DATA_UNAVAILABLE__"
CURRENT_SEASON = datetime.now().year
PREMIER_LEAGUE_ID = 39

TEAM_ALIASES = {
    "liverpool": {"name": "Liverpool", "id": 40},
    "liverpool fc": {"name": "Liverpool", "id": 40},
    "arsenal": {"name": "Arsenal", "id": 42},
    "chelsea": {"name": "Chelsea", "id": 49},
    "man city": {"name": "Manchester City", "id": 50},
    "manchester city": {"name": "Manchester City", "id": 50},
    "man united": {"name": "Manchester United", "id": 33},
    "manchester united": {"name": "Manchester United", "id": 33},
    "tottenham": {"name": "Tottenham", "id": 47},
    "spurs": {"name": "Tottenham", "id": 47},
}

PLAYER_ALIASES = {
    "salah": "Mohamed Salah",
    "mo salah": "Mohamed Salah",
    "mohamed salah": "Mohamed Salah",
    "chiesa": "Federico Chiesa",
    "federico chiesa": "Federico Chiesa",
}


def _log(message: str):
    print(f"[API] {_safe_error(message)}")


def _safe_error(error) -> str:
    text = str(error)
    text = re.sub(r"([?&](?:apiKey|apikey|key|token)=)[^&\s)]+", r"\1***", text, flags=re.IGNORECASE)
    text = re.sub(r"(x-api-key['\"]?\s*:\s*['\"]?)[^,'\"\s)]+", r"\1***", text, flags=re.IGNORECASE)
    text = re.sub(r"(Authorization['\"]?\s*:\s*['\"]?Bearer\s+)[^,'\"\s)]+", r"\1***", text, flags=re.IGNORECASE)
    return text


def _summarize_with_groq(query: str, context: str, intent: str) -> str:
    config = get_config()
    model = get_latest_groq_model()
    api_key = config.groq_api_key
    if not api_key or not model:
        _log(f"Groq summarization skipped: API key or model missing for intent '{intent}'")
        return ""

    try:
        client = Groq(api_key=api_key, base_url=config.groq_base_url)
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        """You are Marcus, the ValleyMind-AI character. "
                                                "Summarize the provided context in your natural, warm, and direct voice. "
                                                "NEVER mention any news source names, URLs, or dates. "
                                                "NEVER say "reputable sources" or "you can find". "
                                                "NEVER suggest checking other websites. NEVER say "I recommend checking". NEVER say "for more information". "
                                                "Strictly 3-4 sentences maximum. "
                                                "Speak as Marcus who already knows this information, with confidence, as if he knows everything. "
                                                "Do not suggest where to find more info. "
                                                "If the context is insufficient, state that the information is not currently available or confirmed. "
                                                "End your summary with a natural sentence: 'Want me to go deeper on any of these?'"""                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Original user query: {query}\n"
                        f"Context intent: {intent}\n"
                        f"Information to summarize:\n{context}\n\n"
                        "Summarize this in 3-4 sentences, in Marcus' voice, without mentioning any APIs, backend, URLs, or dates."
                    ),
                },
            ],
            model=model,
            temperature=0.3,
            max_tokens=256,
        )
        summary = chat_completion.choices[0].message.content
        _log(f"Groq summarization successful for intent '{intent}'")
        return summary
    except Exception as exc:
        _log(f"Groq summarization failed for intent '{intent}': {_safe_error(exc)}")
        return ""


def _configured_news_key() -> str:
    config = get_config()
    return (
        os.getenv("NEWS_API_1", "").strip()
        or os.getenv("NEWS_API_KEY", "").strip()
        or os.getenv("API_KEY", "").strip()
        or config.news_api_1
        or config.news_api_key
        or config.api_key
    )


def _configured_news_key_2() -> str:
    config = get_config()
    return (
        os.getenv("NEWS_API_2", "").strip()
        or os.getenv("NEWSCATCHER_API_KEY", "").strip()
        or os.getenv("CURRENTS_API_KEY", "").strip()
        or config.news_api_2
        or config.newscatcher_api_key
        or config.currents_api_key
    )


def _configured_currents_key() -> str:
    config = get_config()
    return (
        os.getenv("CURRENTS_API_KEY", "").strip()
        or config.currents_api_key
        or os.getenv("API_KEY", "").strip()
        or config.api_key
    )


def _configured_newscatcher_key() -> str:
    config = get_config()
    return (
        os.getenv("NEWSCATCHER_API_KEY", "").strip()
        or config.newscatcher_api_key
        or os.getenv("API_KEY", "").strip()
        or config.api_key
    )


def _configured_sports_key() -> str:
    config = get_config()
    return (
        os.getenv("SPORTS_API_KEY", "").strip()
        or os.getenv("API_SPORTS_KEY", "").strip()
        or os.getenv("API_KEY", "").strip()
        or config.sports_api_key
        or config.api_sports_key
        or config.api_key
    )


def _require_sports_key() -> str:
    api_key = _configured_sports_key()
    if not api_key:
        raise RuntimeError("SPORTS_API_KEY is not configured")
    if len(api_key) < 12:
        raise RuntimeError("SPORTS_API_KEY appears invalid")
    return api_key


def _log_sports_response(response=None, data=None):
    if response is not None:
        print("SPORTS_API_STATUS:", response.status_code)
    if data is not None:
        try:
            sample = json.dumps(data, ensure_ascii=True)[:500]
        except TypeError:
            sample = str(data)[:500]
        print("SPORTS_API_DATA:", sample)


def graceful_live_failure(intent: str) -> str:
    if intent == "sports":
        return "Live sports data is unavailable right now, so I cannot verify the current result or fixture."
    if intent == "search":
        return "I'm having trouble looking that up right now. The information might not be currently available, or I could be experiencing a temporary connection issue."
    if intent == "news":
        return "Live news data is unavailable right now, so I cannot verify the latest update."
    return "Live data is unavailable right now, so I cannot verify the current information."


def classify_live_request(message: str) -> str:
    config = get_config()
    model = get_latest_groq_model()
    api_key = config.groq_api_key
    if not api_key or not model:
        _log("LLM classification skipped: API key or model missing")
        return "CHAT"

    SYSTEM_PROMPT = (
        "You classify user messages as 'CHAT', 'SEARCH', or 'IMAGE_GEN' based on SEMANTIC INTENT, not keyword matching.\n\n"
        "Return 'IMAGE_GEN' when the user wants to create, generate, render, draw, or visualize something as an image — "
        "e.g. 'generate a futuristic city', 'create an image of a dragon', 'make a picture of a cyberpunk AI assistant', "
        "'render a landscape', 'draw a character concept'. The key signal is requesting visual/synthetic media production.\n\n"
        "Return 'SEARCH' only when the user's actual intent is to find out what is currently true in the real world "
        "right now — current news, live scores, current prices, who currently holds a position, recent events, "
        "or anything where the answer could have changed recently and the user wants the current real-world fact.\n\n"
        "Return 'CHAT' for everything else, including: personal decisions, plans, opinions, strategy questions, "
        "requests for advice or pushback, hypothetical scenarios, questions about the user's own project or business, "
        "emotional or relationship topics, technical/coding help, and general knowledge that doesn't change over time.\n\n"
        "Important nuance: a user saying 'imagine a world where...' or 'imagine if...' is CHAT (hypothetical reasoning), "
        "not IMAGE_GEN. Only classify as IMAGE_GEN when they explicitly want a picture/image rendered.\n\n"
        "Judge by what the user is actually trying to accomplish, not by whether the message contains a topic word "
        "that sounds current (AI, money, technology, social media, etc.). A message can mention any trendy topic "
        "while still being a CHAT — for example, 'should I invest in AI video generation' is CHAT (a decision/opinion request), "
        "while 'what is the current price of AI video generation tools' is SEARCH (a real-world fact request).\n\n"
        "Respond with exactly ONE word: CHAT, SEARCH, or IMAGE_GEN."
    )

    try:
        client = Groq(api_key=api_key, base_url=config.groq_base_url)
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            model=model,
            temperature=0.1,
            max_tokens=10,
        )
        result = chat_completion.choices[0].message.content.strip().upper()
        print(f"[ROUTER AGENT] Classified user intent as: {result}")
        _log(f"LLM classification result: '{result}'")
        if result == "SEARCH":
            return "search"
        if result == "IMAGE_GEN":
            return "image"
        return "none"
    except Exception as exc:
        _log(f"LLM classification failed, defaulting to CHAT: {_safe_error(exc)}")
        return "none"


def _sports_topic(message: str) -> str:
    text = str(message or "").lower()
    if re.search(r"\b(history|historical|old|past|previous|all[- ]time|career|legend|won in|final in|season \d{4}|19\d{2}|20[0-2]\d)\b", text):
        return "history"
    if re.search(r"\b(table|standings|rankings?)\b", text):
        return "standings"
    if re.search(r"\b(training|train|session|practice)\b", text):
        return "training"
    if re.search(r"\b(injury|injured|return|coming back|fitness|available)\b", text):
        return "injury"
    if re.search(r"\b(replace|replacement|transfer|sign|target|rumour|rumor)\b", text):
        return "transfer"
    if re.search(r"\b(next match|next game|fixture|fixtures|who will play|lineup|line-up|starting)\b", text):
        return "next_match"
    if re.search(r"\b(win|winner|likely|prediction|title|champion|league)\b", text):
        return "prediction"
    if re.search(r"\b(score|result|live)\b", text):
        return "live_score"
    return "general"


def extract_sports_entities(message: str) -> dict:
    text = str(message or "").lower()
    teams = []
    players = []
    competitions = []

    for alias, team in TEAM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            teams.append(team)

    for alias, player in PLAYER_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            players.append(player)

    if re.search(r"\b(premier league|epl|english premier league)\b", text):
        competitions.append({"name": "Premier League", "id": PREMIER_LEAGUE_ID})

    if players and not teams and any(player in {"Mohamed Salah", "Federico Chiesa"} for player in players):
        teams.append(TEAM_ALIASES["liverpool"])

    seen_team_ids = set()
    unique_teams = []
    for team in teams:
        if team["id"] not in seen_team_ids:
            unique_teams.append(team)
            seen_team_ids.add(team["id"])

    return {
        "topic": _sports_topic(message),
        "teams": unique_teams,
        "players": list(dict.fromkeys(players)),
        "competitions": competitions,
    }


def _trim(text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_items(source: str, items: list) -> str:
    lines = []
    for item in items[:5]:
        title = _trim(item.get("title", ""), 140)
        summary = _trim(item.get("summary", ""), 260)
        url = _trim(item.get("url", ""), 220)
        published = _trim(item.get("published", ""), 80)
        line = f"- {title}"
        if summary:
            line += f": {summary}"
        lines.append(line)
    return f"{source}:\n" + "\n".join(lines) if lines else ""


def _search_currents(query: str) -> str:
    api_key = _configured_currents_key()
    if not api_key:
        _log("Currents skipped: no configured news key")
        return ""

    _log("Calling Currents news API")
    response = requests.get(
        "https://api.currentsapi.services/v1/search",
        params={
            "keywords": query,
            "language": "en",
            "apiKey": api_key,
        },
        timeout=12,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Currents HTTP {response.status_code}: {response.text[:240]}")

    data = response.json()
    articles = data.get("news") or []
    items = [
        {
            "title": article.get("title"),
            "summary": article.get("description"),
            "url": article.get("url"),
            "published": article.get("published"),
        }
        for article in articles
    ]
    _log(f"Currents success: {len(items)} articles")
    return _format_items("Live news results", items)


def _search_newscatcher(query: str) -> str:
    api_key = _configured_newscatcher_key()
    if not api_key:
        _log("Newscatcher skipped: no configured news key")
        return ""

    _log("Calling Newscatcher API")
    response = requests.get(
        "https://api.newscatcherapi.com/v2/search",
        params={"q": query, "lang": "en", "page_size": 5},
        headers={"x-api-key": api_key},
        timeout=12,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Newscatcher HTTP {response.status_code}: {response.text[:240]}")

    data = response.json()
    articles = data.get("articles") or []
    items = [
        {
            "title": article.get("title"),
            "summary": article.get("summary") or article.get("excerpt"),
            "url": article.get("link"),
            "published": article.get("published_date"),
        }
        for article in articles
    ]
    _log(f"Newscatcher success: {len(items)} articles")
    return _format_items("Live news results", items)


def _football_news_query(message: str, entities: dict) -> str:
    parts = []
    for team in entities.get("teams", []):
        parts.append(team["name"])
    parts.extend(entities.get("players", []))
    for competition in entities.get("competitions", []):
        parts.append(competition["name"])
    if not parts:
        parts.append(message)

    topic = entities.get("topic", "general")
    topic_terms = {
        "training": "training session latest news",
        "injury": "injury return fitness latest news",
        "transfer": "transfer replacement latest news",
        "next_match": "next match lineup fixture latest news",
        "prediction": "title race prediction current form latest news",
        "live_score": "live score result",
        "general": "football latest news",
    }
    return " ".join(parts + [topic_terms.get(topic, "football latest news")])


def _search_football_news(message: str, entities: dict) -> str:
    query = _football_news_query(message, entities)
    contexts = []
    for provider in (_search_newscatcher, _search_currents):
        try:
            context = provider(query)
            if context and context != LIVE_DATA_UNAVAILABLE:
                contexts.append(context)
        except Exception as exc:
            _log(f"Football news lookup failed: {_safe_error(exc)}")
        if contexts:
            break
    return "\n\n".join(contexts)


def _search_newsapi(query: str) -> str:
    api_key = _configured_news_key()
    if not api_key:
        _log("Primary news API skipped: API_KEY missing")
        return ""

    params = {
        "apiKey": api_key,
        "language": "en",
        "pageSize": 5,
    }
    endpoint = "https://newsapi.org/v2/everything"
    if re.search(r"\bbbc\b|\bbbc news\b", query, re.IGNORECASE):
        endpoint = "https://newsapi.org/v2/top-headlines"
        params["sources"] = "bbc-news"
    else:
        params["q"] = query
        params["sortBy"] = "publishedAt"

    _log("Calling primary news API")
    response = requests.get(endpoint, params=params, timeout=12)
    if response.status_code != 200:
        raise RuntimeError(f"NewsAPI HTTP {response.status_code}: {response.text[:240]}")

    data = response.json()
    articles = data.get("articles") or []
    items = [
        {
            "title": article.get("title"),
            "summary": article.get("description"),
            "url": article.get("url"),
            "published": article.get("publishedAt"),
        }
        for article in articles
    ]
    _log(f"Primary news API success: {len(items)} articles")
    return _format_items("Live news results", items)


def _tinyfish_api_key() -> str:
    return os.getenv("TINYFISH_API_KEY", "").strip() or os.getenv("TF_API_KEY", "").strip()


def _search_tinyfish(query: str) -> str:
    api_key = _tinyfish_api_key()
    if not api_key:
        _log("TinyFish skipped: TINYFISH_API_KEY not set")
        return ""

    current_date_str = datetime.now().strftime("%B %Y")
    execution_query = f"{query} {current_date_str} news updates"
    _log(f"TinyFish query: {execution_query}")

    import urllib.request as _urllib_req
    import urllib.parse as _urllib_parse

    encoded = _urllib_parse.quote(execution_query)
    url = f"https://api.search.tinyfish.ai?query={encoded}&location=US&language=en"

    raw = None
    # Attempt 1: urllib.request (native, lightweight)
    try:
        req = _urllib_req.Request(url, headers={"X-API-Key": api_key})
        with _urllib_req.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        _log("TinyFish urllib success")
    except Exception as exc:
        _log(f"TinyFish urllib failed ({_safe_error(exc)[:80]}), trying requests fallback")
        try:
            import requests as _requests_fb
            resp = _requests_fb.get(
                "https://api.search.tinyfish.ai",
                params={"query": execution_query, "location": "US", "language": "en"},
                headers={"X-API-Key": api_key},
                timeout=25,
            )
            if resp.status_code == 200:
                raw = resp.text
                _log("TinyFish requests fallback success")
            else:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:240]}")
        except Exception as exc2:
            raise RuntimeError(f"TinyFish failed (urllib + requests fallback): {_safe_error(exc2)}") from exc2

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"TinyFish invalid JSON: {_safe_error(exc)}") from exc

    results = data.get("results") or []
    if not results:
        raise RuntimeError("TinyFish returned no results")

    items = [
        {
            "title": r.get("title", ""),
            "summary": r.get("snippet", ""),
            "url": r.get("url", ""),
            "published": "",
        }
        for r in results[:5]
    ]
    _log(f"TinyFish success: {len(items)} results")
    return _format_items("Live search results", items)


def _search_general_web(query: str) -> str:
    try:
        tf_context = _search_tinyfish(query)
        if tf_context and tf_context != LIVE_DATA_UNAVAILABLE:
            return tf_context
    except Exception as exc:
        _log(f"TinyFish failed: {_safe_error(exc)}")

    return LIVE_DATA_UNAVAILABLE


def _search_api_sports(query: str) -> str:
    try:
        api_key = _require_sports_key()
    except RuntimeError as exc:
        _log(f"API-SPORTS skipped: {_safe_error(exc)}")
        return LIVE_DATA_UNAVAILABLE

    entities = extract_sports_entities(query)
    allow_global = (
        not entities["teams"]
        and not entities["players"]
        and re.search(r"\b(live scores?|all matches|all games|football scores?)\b", query, re.IGNORECASE)
    )
    if entities["teams"] or entities["players"]:
        sections = []
        for team in entities["teams"][:2]:
            fixtures = _team_fixtures_context(team, "live_score")
            if fixtures:
                sections.append(fixtures)
        if sections:
            return "\n\n".join(sections)
        return LIVE_DATA_UNAVAILABLE
    if not allow_global:
        _log("API-SPORTS global fixture dump skipped: no explicit global live-score request")
        return LIVE_DATA_UNAVAILABLE

    _log("Calling API-SPORTS football live fixtures")
    try:
        response = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"live": "all"},
            headers={"x-apisports-key": api_key},
            timeout=12,
        )
        _log_sports_response(response=response)
        data = response.json()
        _log_sports_response(data=data)
    except requests.exceptions.Timeout:
        _log("API-SPORTS timeout while fetching live fixtures")
        return LIVE_DATA_UNAVAILABLE
    except requests.exceptions.RequestException as exc:
        _log(f"API-SPORTS request failed: {_safe_error(exc)}")
        return LIVE_DATA_UNAVAILABLE
    except ValueError as exc:
        _log(f"API-SPORTS invalid JSON: {_safe_error(exc)}")
        return LIVE_DATA_UNAVAILABLE

    if response.status_code == 429:
        _log("API-SPORTS rate limited")
        return LIVE_DATA_UNAVAILABLE
    if response.status_code != 200:
        _log(f"API-SPORTS HTTP {response.status_code}: {response.text[:240]}")
        return LIVE_DATA_UNAVAILABLE

    fixtures = data.get("response") or []
    if not isinstance(fixtures, list):
        _log("API-SPORTS invalid fixture data: response was not a list")
        return LIVE_DATA_UNAVAILABLE
    items = []
    for fixture in fixtures[:5]:
        teams = fixture.get("teams") or {}
        goals = fixture.get("goals") or {}
        status = ((fixture.get("fixture") or {}).get("status") or {}).get("long", "")
        league = (fixture.get("league") or {}).get("name", "")
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        title = f"{home} {goals.get('home')} - {goals.get('away')} {away}".strip()
        items.append({
            "title": title,
            "summary": f"{league} - {status}",
            "url": "",
            "published": "",
        })
    _log(f"API-SPORTS success: {len(items)} live fixtures")
    if not items:
        return "Live sports results:\n- No live football fixtures were returned."
    return _format_items("Live sports results", items)


def _api_sports_get(path: str, params: dict) -> dict:
    api_key = _require_sports_key()

    try:
        response = requests.get(
            f"https://v3.football.api-sports.io/{path.lstrip('/')}",
            params=params,
            headers={"x-apisports-key": api_key},
            timeout=12,
        )
        _log_sports_response(response=response)
        data = response.json()
        _log_sports_response(data=data)
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("API-SPORTS timeout") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"API-SPORTS request failed: {_safe_error(exc)}") from exc
    except ValueError as exc:
        raise RuntimeError("API-SPORTS returned invalid JSON") from exc

    if response.status_code == 429:
        raise RuntimeError("API-SPORTS rate limited")
    if response.status_code != 200:
        raise RuntimeError(f"API-SPORTS HTTP {response.status_code}: {response.text[:240]}")
    if not isinstance(data, dict):
        raise RuntimeError("API-SPORTS returned invalid data")
    return data


def _format_fixture(fixture: dict) -> str:
    if not isinstance(fixture, dict):
        return ""
    teams = fixture.get("teams") or {}
    goals = fixture.get("goals") or {}
    info = fixture.get("fixture") or {}
    status = (info.get("status") or {}).get("long", "")
    date = info.get("date", "")
    league = (fixture.get("league") or {}).get("name", "")
    home = (teams.get("home") or {}).get("name", "")
    away = (teams.get("away") or {}).get("name", "")
    score = ""
    if goals.get("home") is not None or goals.get("away") is not None:
        score = f" {goals.get('home')} - {goals.get('away')}"
    if not home or not away:
        return ""
    return _trim(f"{home}{score} {away} | {league} | {status} | {date}", 260)


def _team_search_context(message: str, entities: dict) -> tuple[list, str]:
    candidates = [team["name"] for team in entities.get("teams", []) if team.get("name")]
    if not candidates:
        cleaned = re.sub(
            r"\b(match|matches|fixture|fixtures|score|scores|table|standings|news|latest|today|now|football|soccer|epl|premier league|who scored|club|team|tell me about)\b",
            " ",
            str(message or ""),
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            candidates.append(cleaned)

    resolved = []
    lines = []
    seen = set()
    for candidate in candidates[:2]:
        try:
            data = _api_sports_get("teams", {"search": candidate})
        except Exception as exc:
            _log(f"Team search failed for '{candidate}': {_safe_error(exc)}")
            continue
        teams = data.get("response") or []
        if not isinstance(teams, list):
            _log("API-SPORTS invalid team search data")
            continue
        for row in teams[:3]:
            team = (row or {}).get("team") or {}
            team_id = team.get("id")
            name = team.get("name")
            country = team.get("country", "")
            if not team_id or not name or team_id in seen:
                continue
            seen.add(team_id)
            resolved.append({"name": name, "id": team_id})
            lines.append(_trim(f"{name} | {country} | id {team_id}", 180))
            break
    context = "Team search context:\n" + "\n".join(f"- {line}" for line in lines) if lines else ""
    return resolved, context


def _team_fixtures_context(team: dict, topic: str) -> str:
    if not team.get("id"):
        return ""
    params = {"team": team["id"]}
    if topic in {"next_match", "prediction", "general"}:
        params["next"] = 5
    elif topic in {"live_score"}:
        params["live"] = "all"
    else:
        params["next"] = 3

    data = _api_sports_get("fixtures", params)
    fixtures = data.get("response") or []
    relevant = []
    for fixture in fixtures:
        teams = fixture.get("teams") or {}
        names = {
            ((teams.get("home") or {}).get("name") or "").lower(),
            ((teams.get("away") or {}).get("name") or "").lower(),
        }
        if team["name"].lower() in names:
            relevant.append(fixture)
    if not relevant:
        return ""
    lines = [line for line in (_format_fixture(fixture) for fixture in relevant[:5]) if line]
    if not lines:
        return ""
    return "Relevant fixtures:\n" + "\n".join(f"- {line}" for line in lines)


def _premier_league_standings_context() -> str:
    data = _api_sports_get("standings", {"league": PREMIER_LEAGUE_ID, "season": CURRENT_SEASON})
    standings = ((data.get("response") or [{}])[0].get("league") or {}).get("standings") or []
    table = standings[0] if standings else []
    if not table:
        return ""
    lines = []
    for row in table[:8]:
        team = (row.get("team") or {}).get("name", "")
        rank = row.get("rank", "")
        points = row.get("points", "")
        form = row.get("form", "")
        lines.append(f"{rank}. {team} - {points} pts - form {form}")
    return "Premier League table context:\n" + "\n".join(f"- {line}" for line in lines)


def sports_context_for_question(message: str) -> str:
    entities = extract_sports_entities(message)
    searched_teams, team_search = _team_search_context(message, entities)
    if searched_teams:
        known_ids = {team.get("id") for team in entities["teams"]}
        for team in searched_teams:
            if team.get("id") not in known_ids:
                entities["teams"].append(team)
                known_ids.add(team.get("id"))
    sections = [
        f"Sports topic: {entities['topic']}",
        "Teams: " + ", ".join(team["name"] for team in entities["teams"]) if entities["teams"] else "Teams: none explicit",
        "Players: " + ", ".join(entities["players"]) if entities["players"] else "Players: none explicit",
        "Competitions: " + ", ".join(comp["name"] for comp in entities["competitions"]) if entities["competitions"] else "Competitions: none explicit",
    ]
    if team_search:
        sections.append(team_search)

    try:
        if entities["topic"] not in {"next_match", "live_score", "prediction", "general", "standings"}:
            for team in entities["teams"][:2]:
                fixtures = _team_fixtures_context(team, entities["topic"])
                if fixtures:
                    sections.append(fixtures)
            if any(comp.get("id") == PREMIER_LEAGUE_ID for comp in entities["competitions"]):
                standings = _premier_league_standings_context()
                if standings:
                    sections.append(standings)
        if entities["topic"] in {"next_match", "live_score", "prediction", "general", "standings"}:
            for team in entities["teams"][:2]:
                fixtures = _team_fixtures_context(team, entities["topic"])
                if fixtures:
                    sections.append(fixtures)
            if entities["topic"] == "live_score" and not entities["teams"]:
                live_scores = _search_api_sports(message)
                if live_scores and live_scores != LIVE_DATA_UNAVAILABLE:
                    sections.append(live_scores)
            if any(comp.get("id") == PREMIER_LEAGUE_ID for comp in entities["competitions"]) or entities["topic"] in {"prediction", "standings"}:
                standings = _premier_league_standings_context()
                if standings:
                    sections.append(standings)
    except Exception as exc:
        _log(f"Relevant sports data lookup failed: {_safe_error(exc)}")

    if entities["topic"] in {"training", "injury", "transfer", "prediction", "general", "next_match"}:
        news = _search_football_news(message, entities)
        if news:
            sections.append(news)

    if len(sections) <= 4:
        return LIVE_DATA_UNAVAILABLE
    return "\n\n".join(sections)


def _search_news_only(query: str) -> str:
    failures = []

    # Layer 1: NEWS_API_1 (primary news API)
    if _configured_news_key():
        try:
            _log("[NEWS PIPELINE] Layer 1: NEWS_API_1")
            context = _search_newsapi(query)
            if context and context != LIVE_DATA_UNAVAILABLE:
                return context
        except Exception as exc:
            reason = f"NEWS_API_1 failed: {_safe_error(exc)}"
            failures.append(reason)
            _log(reason)
    else:
        _log("[NEWS PIPELINE] Layer 1 skipped: NEWS_API_1 not configured")

    # Layer 2: NEWS_API_2 (secondary news API — Currents / Newscatcher)
    if _configured_news_key_2():
        for name, provider in (("Currents", _search_currents), ("Newscatcher", _search_newscatcher)):
            try:
                _log(f"[NEWS PIPELINE] Layer 2: {name} (NEWS_API_2)")
                context = provider(query)
                if context and context != LIVE_DATA_UNAVAILABLE:
                    return context
            except Exception as exc:
                reason = f"{name} (NEWS_API_2) failed: {_safe_error(exc)}"
                failures.append(reason)
                _log(reason)
    else:
        _log("[NEWS PIPELINE] Layer 2 skipped: NEWS_API_2 not configured")

    # Layer 3: TinyFish web search fallback
    _log("[NEWS PIPELINE] Layer 3: TinyFish web search")
    try:
        tf_context = _search_tinyfish(query)
        if tf_context and tf_context != LIVE_DATA_UNAVAILABLE:
            return tf_context
    except Exception as exc:
        reason = f"TinyFish (news fallback) failed: {_safe_error(exc)}"
        failures.append(reason)
        _log(reason)

    if failures:
        _log("[NEWS PIPELINE] All layers failed: " + " | ".join(failures))
    return LIVE_DATA_UNAVAILABLE


def _search_sports_only(query: str) -> str:
    entities = extract_sports_entities(query)
    if entities.get("topic") == "history":
        return LIVE_DATA_UNAVAILABLE

    # Layer 1: SPORTS_API_KEY (dedicated sports API — API-SPORTS / football)
    if _configured_sports_key():
        try:
            _log("[SPORTS PIPELINE] Layer 1: SPORTS_API_KEY")
            context = sports_context_for_question(query)
            if context and context != LIVE_DATA_UNAVAILABLE:
                return context
        except Exception as exc:
            _log(f"[SPORTS PIPELINE] Layer 1 (SPORTS_API_KEY) failed: {_safe_error(exc)}")
    else:
        _log("[SPORTS PIPELINE] Layer 1 skipped: SPORTS_API_KEY not configured")

    # Layer 2: TinyFish web search
    _log("[SPORTS PIPELINE] Layer 2: TinyFish web search")
    try:
        tf_context = _search_tinyfish(query)
        if tf_context and tf_context != LIVE_DATA_UNAVAILABLE:
            return tf_context
    except Exception as exc:
        _log(f"[SPORTS PIPELINE] TinyFish failed: {_safe_error(exc)}")

    return LIVE_DATA_UNAVAILABLE


def strict_live_context(message: str) -> dict:
    message = str(message or "").strip()
    try:
        context = _search_general_web(message)
    except Exception as exc:
        reason = _safe_error(exc)
        _log(f"Strict search route failed: {reason}")
        return {"intent": "search", "context": LIVE_DATA_UNAVAILABLE, "error": reason}

    if not context or context == LIVE_DATA_UNAVAILABLE:
        return {"intent": "search", "context": LIVE_DATA_UNAVAILABLE, "error": "No API provider returned usable data"}
    return {
        "intent": "search",
        "context": f"Live search context gathered at {datetime.now().isoformat()}.\n{context}",
        "error": "",
    }


def live_api_answer(message: str) -> str:
    message = str(message or "").strip()
    request_type = classify_live_request(message)
    context = ""
    if request_type == "sports":
        context = _search_sports_only(message)
    elif request_type == "search":
        context = _search_general_web(message)
    elif request_type == "news":
        context = _search_news_only(message)
    else:
        context = _search_general_web(message)

    if context == LIVE_DATA_UNAVAILABLE or not context:
        return graceful_live_failure(request_type)

    summarized_context = _summarize_with_groq(message, context, request_type)
    if summarized_context:
        return summarized_context

    # Fallback to simple line parsing if Groq summarization fails
    lines = []
    for line in context.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("Live external context gathered"):
            continue
        if cleaned.startswith("Live ") and cleaned.endswith("results:"):
            continue
        if cleaned.startswith("- "):
            lines.append(cleaned[2:])
        if len(lines) >= 5:
            break

    if not lines:
        return "I found live results, but there were no clear details to summarize yet."

    intro = "Here's what I found:"
    if request_type == "sports":
        intro = "Here are the latest sports updates I found:"
    elif request_type == "search":
        intro = "Here's what I found on that topic:"
    elif request_type == "news":
        intro = "Here are the latest news updates I found:"
    return intro + "\n" + "\n".join(f"- {line}" for line in lines)


def get_external_context(message: str) -> str:
    message = str(message or "").strip()
    if not message:
        _log("External context skipped: empty prompt")
        return ""

    contexts = []
    failures = []
    query = message

    _log(f"External context requested for: {query}")

    request_type = classify_live_request(message)
    providers = []
    if request_type == "sports":
        providers.append(("Context-aware sports", sports_context_for_question))
    elif request_type == "news":
        providers.append(("News route", _search_news_only))

    for name, provider in providers:
        try:
            context = provider(query)
            if context and context != LIVE_DATA_UNAVAILABLE:
                contexts.append(context)
                if len(contexts) >= 2:
                    break
        except Exception as exc:
            reason = f"{name} failed: {_safe_error(exc)}"
            failures.append(reason)
            _log(reason)

    if contexts:
        _log(f"External context success via {len(contexts)} provider(s)")
        return (
            f"Live external context gathered at {datetime.now().isoformat()}.\n"
            + "\n\n".join(contexts)
        )

    if failures:
        _log("External fallback trigger reason: " + " | ".join(failures))
    else:
        _log("External fallback trigger reason: no provider returned results")
    return LIVE_DATA_UNAVAILABLE
