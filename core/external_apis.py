import json
import os
import re
from datetime import datetime
import requests

from core.config import get_config


CURRENT_PATTERNS = [
    r"\b(today|now|latest|current|currently|right now|recent|breaking|news|headline|live|score|sports|match|game)\b",
    r"\b(what happened|who won|bbc news|bbc|updates?|this week|this month|202[5-9]|203[0-9])\b",
    r"\b(ai|tech|technology|politics|finance|market|stocks|crypto)\s+(news|updates?|latest|today|now|current)\b",
    r"\b(who is|who's|where is|what is|what's)\s+(the\s+)?(president|prime minister|ceo|leader|governor|mayor|price|rate|weather|score)\b",
    r"\b(stock price|share price|exchange rate|weather in|forecast for)\b",
]

SPORTS_PATTERNS = [
    r"\b(sports|score|fixture|fixtures|match|game|league|team|player|players|transfer|nba|nfl|mlb|nhl|soccer|football|liverpool|epl|premier league|champions league)\b",
]

NEWS_PATTERNS = [
    r"\b(news|headline|breaking|latest|updates?|current events|today|recent|bbc news|bbc|politics|finance|tech|technology|ai|world events|market|stocks|crypto)\b",
]


LIVE_DATA_UNAVAILABLE = "__LIVE_DATA_UNAVAILABLE__"
CURRENT_SEASON = 2025
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


def _configured_news_key() -> str:
    config = get_config()
    return (
        os.getenv("NEWS_API_KEY", "").strip()
        or os.getenv("API_KEY", "").strip()
        or config.news_api_key
        or config.api_key
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
    if intent in {"news", "live"}:
        return "Live news data is unavailable right now, so I cannot verify the latest update."
    return "Live data is unavailable right now, so I cannot verify the current information."


def needs_external_context(message: str) -> bool:
    text = str(message or "").lower()
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in CURRENT_PATTERNS)


def _is_sports(message: str) -> bool:
    return any(re.search(pattern, message, re.IGNORECASE) for pattern in SPORTS_PATTERNS)


def _is_news(message: str) -> bool:
    return any(re.search(pattern, message, re.IGNORECASE) for pattern in NEWS_PATTERNS)


def classify_live_request(message: str) -> str:
    sports_entities = extract_sports_entities(message)
    if sports_entities["teams"] or sports_entities["players"] or sports_entities["competitions"]:
        return "sports"
    if _is_sports(message):
        return "sports"
    if _is_news(message):
        return "news"
    if needs_external_context(message):
        return "live"
    return ""


def _sports_topic(message: str) -> str:
    text = str(message or "").lower()
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
        if published:
            line += f" ({published})"
        if summary:
            line += f": {summary}"
        if url:
            line += f" Source: {url}"
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


def _search_duckduckgo(query: str) -> str:
    _log("Calling DuckDuckGo search")
    try:
        from duckduckgo_search import DDGS
    except ImportError as exc:
        raise RuntimeError(f"duckduckgo_search is not installed: {exc}") from exc

    items = []
    with DDGS(timeout=10) as ddgs:
        for result in ddgs.text(query, max_results=5):
            items.append({
                "title": result.get("title"),
                "summary": result.get("body"),
                "url": result.get("href"),
                "published": "",
            })
    _log(f"DuckDuckGo success: {len(items)} results")
    return _format_items("Live search results", items)


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
        if entities["topic"] not in {"next_match", "live_score", "prediction", "general"}:
            for team in entities["teams"][:2]:
                fixtures = _team_fixtures_context(team, entities["topic"])
                if fixtures:
                    sections.append(fixtures)
            if any(comp.get("id") == PREMIER_LEAGUE_ID for comp in entities["competitions"]):
                standings = _premier_league_standings_context()
                if standings:
                    sections.append(standings)
        if entities["topic"] in {"next_match", "live_score", "prediction", "general"}:
            for team in entities["teams"][:2]:
                fixtures = _team_fixtures_context(team, entities["topic"])
                if fixtures:
                    sections.append(fixtures)
            if entities["topic"] == "live_score" and not entities["teams"]:
                live_scores = _search_api_sports(message)
                if live_scores and live_scores != LIVE_DATA_UNAVAILABLE:
                    sections.append(live_scores)
            if any(comp.get("id") == PREMIER_LEAGUE_ID for comp in entities["competitions"]) or entities["topic"] == "prediction":
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
    contexts = []
    failures = []
    providers = [
        ("Currents", _search_currents),
        ("Newscatcher", _search_newscatcher),
    ]
    if _configured_news_key():
        providers.insert(0, ("Primary news API", _search_newsapi))
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
        return "\n\n".join(contexts)
    if failures:
        _log("News fallback trigger reason: " + " | ".join(failures))
    return LIVE_DATA_UNAVAILABLE


def _search_sports_only(query: str) -> str:
    return sports_context_for_question(query)


def strict_live_context(message: str) -> dict:
    message = str(message or "").strip()
    intent = classify_live_request(message)
    if not intent:
        return {"intent": "", "context": "", "error": ""}

    try:
        if intent == "sports":
            context = _search_sports_only(message)
        elif intent in {"news", "live"}:
            context = _search_news_only(message)
        else:
            context = ""
    except Exception as exc:
        reason = _safe_error(exc)
        _log(f"Strict {intent} route failed: {reason}")
        return {"intent": intent, "context": LIVE_DATA_UNAVAILABLE, "error": reason}

    if not context or context == LIVE_DATA_UNAVAILABLE:
        return {"intent": intent, "context": LIVE_DATA_UNAVAILABLE, "error": "No API provider returned usable data"}
    return {
        "intent": intent,
        "context": f"Live {intent} context gathered at {datetime.now().isoformat()}.\n{context}",
        "error": "",
    }


def live_api_answer(message: str) -> str:
    message = str(message or "").strip()
    request_type = classify_live_request(message)
    if request_type == "sports":
        context = _search_sports_only(message)
    elif request_type == "news":
        context = _search_news_only(message)
    else:
        context = get_external_context(message)

    if context == LIVE_DATA_UNAVAILABLE or not context:
        return graceful_live_failure(request_type)

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

    intro = "Here are the latest updates I found:"
    if request_type == "sports":
        intro = "Here are the latest sports updates I found:"
    elif request_type == "news":
        intro = "Here are the latest news updates I found:"
    return intro + "\n" + "\n".join(f"- {line}" for line in lines)


def get_external_context(message: str) -> str:
    message = str(message or "").strip()
    if not message or not needs_external_context(message):
        _log("External context skipped: prompt does not require live data")
        return ""

    contexts = []
    failures = []
    query = message

    _log(f"External context requested for: {query}")

    request_type = classify_live_request(message)
    providers = []
    if request_type == "sports":
        providers.append(("Context-aware sports", sports_context_for_question))
    elif request_type in {"news", "live"}:
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
