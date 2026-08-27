"""Source Intelligence Layer — research, evidence extraction, source provenance.

This module is an ADD-ON to the existing memory/search infrastructure.
It does NOT replace MemorySystem, MemoryManager, ConversationRecovery,
or the existing search pipeline.  It consumes the existing TinyFish
search infrastructure and adds:

  - Structured search results (not just formatted text)
  - Source tier classification (Tier 1-4)
  - Search → Open → Verify pipeline
  - Evidence extraction and ranking
  - Multi-source verification
  - Source deduplication
  - Research confidence scoring
  - Source freshness detection
  - Domain-aware search strategies
  - Compact evidence packages (provider-agnostic)

The evidence package is designed to survive provider fallbacks — if the
primary LLM fails, the same evidence package is forwarded to the next
provider unchanged.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

EVIDENCE_BUDGET_CHARS = 3000       # max chars of evidence sent to LLM
EVIDENCE_BUDGET_EVIDENCES = 6      # max number of evidence items
SEARCH_RESULT_LIMIT = 10           # max results from a single search
FETCH_TIMEOUT = 15                 # seconds for URL fetch
MAX_FETCH_CHARS = 4000             # max chars to extract from a fetched page

# Time-sensitive keywords that trigger freshness boosting
_FRESHNESS_KEYWORDS = frozenset({
    "latest", "today", "current", "now", "recent", "this week", "this month",
    "this year", "new", "update", "pricing", "limits", "rate limit",
    "api limit", "tpm", "rpm", "status", "outage", "incident",
    "price", "price of", "right now", "as of", "currently",
    "live", "happening", "breaking", "just", "morning", "tonight",
    "yesterday", "tomorrow", "this morning", "this evening",
})


# ═══════════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SourceIdentity:
    """Stable identity for a source website / documentation."""
    source_id: str            # e.g. "groq.com"
    name: str                 # e.g. "Groq"
    domain: str               # e.g. "console.groq.com"
    icon_url: str = ""        # resolved favicon URL
    tier: int = 3             # 1=official, 2=reputable, 3=secondary, 4=unverified
    source_type: str = "web"  # official_documentation, technical_article, news, etc.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    """Structured search result with source identity."""
    title: str = ""
    url: str = ""
    domain: str = ""
    snippet: str = ""
    published: str = ""
    source: Optional[SourceIdentity] = None
    relevance: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.source:
            d["source"] = self.source.to_dict()
        return d


@dataclass
class Evidence:
    """A verified piece of evidence from a source."""
    source_name: str = ""
    source_domain: str = ""
    source_icon: str = ""
    source_type: str = "web"
    url: str = ""
    title: str = ""
    content: str = ""          # extracted relevant passage
    relevance: float = 0.0
    supports_claims: list[str] = field(default_factory=list)
    published: str = ""
    accessed_at: str = ""
    tier: int = 3
    freshness_label: str = ""  # "today" | "yesterday" | "recent" | "old" | ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidencePackage:
    """Compact, provider-agnostic evidence package for the LLM.

    This survives provider fallbacks — if the primary model fails,
    the same package is forwarded to the next provider unchanged.
    """
    evidence: list[Evidence] = field(default_factory=list)
    total_searched: int = 0
    total_opened: int = 0
    total_used: int = 0
    confidence: str = "low"     # high, medium, low
    research_log: list[str] = field(default_factory=list)
    is_research_request: bool = False
    conflicts: list[str] = field(default_factory=list)
    freshness_category: str = ""  # "current" | "background" | ""

    def to_context_string(self, budget: int = EVIDENCE_BUDGET_CHARS) -> str:
        """Render evidence as a compact context string for the LLM."""
        if not self.evidence:
            return ""

        lines = ["[SOURCE INTELLIGENCE — Verified Evidence]", ""]
        from datetime import datetime as _dt
        today_str = _dt.now().strftime("%A, %B %d, %Y")
        lines.append(f"TODAY'S DATE (for comparing source freshness): {today_str}")
        lines.append(
            "IMPORTANT: A source marked 'today' is from today; 'yesterday' is 1 day old; "
            "'recent' is under a week old; 'old' is a week or more old. NEVER relabel an "
            "older source as today's data."
        )
        lines.append("")
        if self.freshness_category == "current":
            lines.append("[DATA TYPE: CURRENT — information reflects the latest available data as of the search time]")
            lines.append("")
        elif self.freshness_category == "background":
            lines.append("[DATA TYPE: BACKGROUND — historical or general knowledge; may not reflect the latest state]")
            lines.append("")
        used = 0
        char_count = 0

        for ev in self.evidence:
            block = (
                f"Source: {ev.source_name} ({ev.source_domain}) "
                f"[Tier {ev.tier}]\n"
                f"URL: {ev.url}\n"
                f"Title: {ev.title}\n"
                f"Evidence: {ev.content}\n"
            )
            if ev.supports_claims:
                block += f"Supports: {', '.join(ev.supports_claims)}\n"
            if ev.published:
                block += f"Published: {ev.published}\n"
            if ev.freshness_label:
                block += f"Freshness: {ev.freshness_label} (relative to today)\n"
            block += "\n"

            if char_count + len(block) > budget:
                break
            lines.append(block)
            char_count += len(block)
            used += 1

        if not used:
            return ""

        confidence_note = {
            "high": "High confidence — primary sources directly confirm the information.",
            "medium": "Medium confidence — multiple reputable sources agree.",
            "low": "Low confidence — limited or conflicting evidence found.",
        }.get(self.confidence, "")

        summary = (
            f"Research summary: {self.total_searched} sources searched, "
            f"{self.total_opened} inspected, {self.total_used} used as evidence. "
            f"{confidence_note}"
        )
        lines.append(summary)

        if self.conflicts:
            lines.append("")
            lines.append("[SOURCE CONFLICTS — requires LLM resolution]")
            for c in self.conflicts:
                lines.append(f"⚠ {c}")
            lines.append("Note: when sources conflict, present both perspectives and note the discrepancy.")

        return "\n".join(lines)

    def to_frontend_metadata(self) -> list[dict]:
        """Render evidence as frontend-friendly source metadata."""
        sources = []
        seen_domains = set()
        for ev in self.evidence:
            if ev.source_domain in seen_domains:
                continue
            seen_domains.add(ev.source_domain)
            sources.append({
                "id": ev.source_domain,
                "name": ev.source_name,
                "title": ev.title,
                "domain": ev.source_domain,
                "url": ev.url,
                "icon": ev.source_icon,
                "source_type": ev.source_type,
                "tier": ev.tier,
                "supports_claims": ev.supports_claims,
                "published": ev.published,
                "freshness_category": self.freshness_category,
                "freshness_label": ev.freshness_label,
            })
        return sources

    def to_dict(self) -> dict:
        return {
            "evidence": [e.to_dict() for e in self.evidence],
            "total_searched": self.total_searched,
            "total_opened": self.total_opened,
            "total_used": self.total_used,
            "confidence": self.confidence,
            "research_log": self.research_log,
            "is_research_request": self.is_research_request,
            "conflicts": self.conflicts,
            "freshness_category": self.freshness_category,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Source tier classification
# ═══════════════════════════════════════════════════════════════════════════════

# Domain → tier mapping (extensible)
_TIER_1_DOMAINS = frozenset({
    "console.groq.com", "groq.com", "platform.openai.com", "openai.com",
    "docs.anthropic.com", "anthropic.com", "developers.google.com",
    "cloud.google.com", "docs.aws.amazon.com", "azure.microsoft.com",
    "github.com", "gitlab.com", "huggingface.co",
    "developer.mozilla.org", "docs.python.org", "docs.rust-lang.org",
    "docs.docker.com", "kubernetes.io", "react.dev", "nextjs.org",
    "developer.apple.com", "learn.microsoft.com",
    "mongodb.com", "redis.io", "postgresql.org", "sqlite.org",
    "cloudflare.com", "developers.cloudflare.com",
    "render.com", "docs.render.com", "vercel.com",
})

_TIER_2_DOMAINS = frozenset({
    "arxiv.org", "acm.org", "ieee.org",
    "stackoverflow.com", "stackexchange.com",
    "medium.com", "dev.to", "hashnode.dev",
    "techcrunch.com", "arstechnica.com", "theverge.com",
    "wikipedia.org", "mit.edu", "stanford.edu", "berkeley.edu",
})

_TIER_4_PATTERNS = re.compile(
    r"(?:reddit\.com|quora\.com|pinterest\.com|facebook\.com|"
    r"tiktok\.com|instagram\.com|twitter\.com|x\.com|"
    r"medium\.com/@|substack\.com/|wordpress\.com|blogspot\.com)",
    re.IGNORECASE,
)

# Known source name overrides (domain → display name)
_SOURCE_NAMES = {
    "groq.com": "Groq", "console.groq.com": "Groq",
    "openai.com": "OpenAI", "platform.openai.com": "OpenAI",
    "anthropic.com": "Anthropic", "docs.anthropic.com": "Anthropic",
    "github.com": "GitHub",
    "developer.mozilla.org": "MDN", "docs.python.org": "Python Docs",
    "stackoverflow.com": "Stack Overflow",
    "arxiv.org": "arXiv",
    "huggingface.co": "Hugging Face",
    "render.com": "Render", "docs.render.com": "Render",
    "cloudflare.com": "Cloudflare", "developers.cloudflare.com": "Cloudflare",
    "mongodb.com": "MongoDB",
    "react.dev": "React", "nextjs.org": "Next.js",
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "arstechnica.com": "Ars Technica",
    "medium.com": "Medium",
    "wikipedia.org": "Wikipedia",
    "dev.to": "DEV.to",
}

# Source type classification keywords
_OFFICIAL_KEYWORDS = re.compile(
    r"(?:docs?\.|documentation|reference|api|guide|tutorial|"
    r"developer|official|learn|manual)", re.IGNORECASE
)
_NEWS_KEYWORDS = re.compile(
    r"(?:news|article|report|breaking|latest|announcement)", re.IGNORECASE
)

# Domain → source_type overrides (financial / market-data sources)
_SOURCE_TYPE_OVERRIDES = {
    "coinbase.com": "exchange_market_data", "binance.com": "exchange_market_data",
    "coinmarketcap.com": "market_data", "coingecko.com": "market_data",
    "finance.yahoo.com": "market_data", "investing.com": "financial_news",
    "marketwatch.com": "financial_news", "cnbc.com": "financial_news",
    "ft.com": "financial_news", "bloomberg.com": "financial_news",
    "reuters.com": "news_agency", "apnews.com": "news_agency",
    "techcrunch.com": "tech_news", "theverge.com": "tech_news",
    "arstechnica.com": "tech_news",
}


def classify_domain(domain: str) -> SourceIdentity:
    """Classify a domain into a SourceIdentity with tier and type."""
    d = domain.lower().replace("www.", "")
    root_domain = d.split(".")[-2] + "." + d.split(".")[-1] if "." in d else d

    if d in _TIER_1_DOMAINS or root_domain in _TIER_1_DOMAINS:
        tier = 1
    elif d in _TIER_2_DOMAINS or root_domain in _TIER_2_DOMAINS:
        tier = 2
    elif _TIER_4_PATTERNS.search(d):
        tier = 4
    else:
        tier = 3

    name = _SOURCE_NAMES.get(d, "")
    if not name:
        name = _SOURCE_NAMES.get(root_domain, "")
    if not name:
        name = d.split(".")[0].capitalize()

    if tier == 1:
        source_type = "official_documentation"
    elif _NEWS_KEYWORDS.search(d):
        source_type = "news"
    else:
        source_type = "web"
    # Apply financial / market-data overrides
    source_type = _SOURCE_TYPE_OVERRIDES.get(root_domain, source_type)
    source_type = _SOURCE_TYPE_OVERRIDES.get(d, source_type)

    return SourceIdentity(
        source_id=root_domain,
        name=name,
        domain=d,
        icon_url=favicon_url(d),
        tier=tier,
        source_type=source_type,
    )


def favicon_url(domain: str) -> str:
    """Resolve a favicon URL for a domain. Safe fallback to empty."""
    d = domain.replace("www.", "")
    return f"https://www.google.com/s2/favicons?sz=32&domain={d}"


# ═══════════════════════════════════════════════════════════════════════════════
# Search query planning
# ═══════════════════════════════════════════════════════════════════════════════

# User-specified source patterns
_SOURCE_SPECIFIED_RE = re.compile(
    r"(?:search|check|look(?:\s+(?:on|at|in))?|find(?:\s+on)?)\s+"
    r"(?:on\s+)?([a-z0-9][a-z0-9.\-]*\.[a-z]{2,}|[A-Z][a-zA-Z]+)\b",
    re.IGNORECASE,
)

# Domain-aware search strategies — when a domain is mentioned, boost it
_DOMAIN_STRATEGIES = {
    # Tech / developer
    "groq": {"domains": ["console.groq.com", "groq.com"], "type": "official_documentation"},
    "openai": {"domains": ["platform.openai.com", "openai.com"], "type": "official_documentation"},
    "anthropic": {"domains": ["docs.anthropic.com", "anthropic.com"], "type": "official_documentation"},
    "github": {"domains": ["github.com"], "type": "code_repository"},
    "hugging face": {"domains": ["huggingface.co"], "type": "model_repository"},
    "render": {"domains": ["render.com", "docs.render.com"], "type": "official_documentation"},
    "cloudflare": {"domains": ["developers.cloudflare.com"], "type": "official_documentation"},
    "mongodb": {"domains": ["mongodb.com/docs"], "type": "official_documentation"},
    "python": {"domains": ["docs.python.org"], "type": "official_documentation"},
    "react": {"domains": ["react.dev"], "type": "official_documentation"},
    "docker": {"domains": ["docs.docker.com"], "type": "official_documentation"},
    "kubernetes": {"domains": ["kubernetes.io"], "type": "official_documentation"},
    "aws": {"domains": ["docs.aws.amazon.com"], "type": "official_documentation"},
    "google cloud": {"domains": ["cloud.google.com", "developers.google.com"], "type": "official_documentation"},
    "azure": {"domains": ["azure.microsoft.com", "learn.microsoft.com"], "type": "official_documentation"},
    # News
    "reuters": {"domains": ["reuters.com"], "type": "news_agency"},
    "bbc": {"domains": ["bbc.com", "bbc.co.uk"], "type": "news"},
    "cnn": {"domains": ["cnn.com"], "type": "news"},
    "associated press": {"domains": ["apnews.com"], "type": "news_agency"},
    "bloomberg": {"domains": ["bloomberg.com"], "type": "financial_news"},
    "techcrunch": {"domains": ["techcrunch.com"], "type": "tech_news"},
    "the verge": {"domains": ["theverge.com"], "type": "tech_news"},
    "ars technica": {"domains": ["arstechnica.com"], "type": "tech_news"},
    # Financial / market data (live price sources)
    "coinbase": {"domains": ["coinbase.com"], "type": "exchange_market_data"},
    "binance": {"domains": ["binance.com"], "type": "exchange_market_data"},
    "coinmarketcap": {"domains": ["coinmarketcap.com"], "type": "market_data"},
    "coingecko": {"domains": ["coingecko.com"], "type": "market_data"},
    "investing": {"domains": ["investing.com"], "type": "financial_news"},
    "marketwatch": {"domains": ["marketwatch.com"], "type": "financial_news"},
    "cnbc": {"domains": ["cnbc.com"], "type": "financial_news"},
    "financial times": {"domains": ["ft.com"], "type": "financial_news"},
    "yahoo finance": {"domains": ["finance.yahoo.com"], "type": "market_data"},
    "forex": {"domains": ["forex.com", "investing.com"], "type": "market_data"},
    # Science
    "pubmed": {"domains": ["pubmed.ncbi.nlm.nih.gov"], "type": "medical_database"},
    "nature": {"domains": ["nature.com"], "type": "scientific_journal"},
    "arxiv": {"domains": ["arxiv.org"], "type": "preprint_repository"},
    "nih": {"domains": ["nih.gov"], "type": "government_research"},
    "cdc": {"domains": ["cdc.gov"], "type": "government_health"},
    # Media / entertainment
    "imdb": {"domains": ["imdb.com"], "type": "entertainment_database"},
    "rotten tomatoes": {"domains": ["rottentomatoes.com"], "type": "review_aggregator"},
    "metacritic": {"domains": ["metacritic.com"], "type": "review_aggregator"},
    "goodreads": {"domains": ["goodreads.com"], "type": "book_database"},
    "steam": {"domains": ["store.steampowered.com"], "type": "game_store"},
    "ign": {"domains": ["ign.com"], "type": "gaming_news"},
    "polygon": {"domains": ["polygon.com"], "type": "gaming_news"},
    # Sports
    "espn": {"domains": ["espn.com"], "type": "sports_news"},
    "bbc sport": {"domains": ["bbc.com/sport"], "type": "sports_news"},
    "transfermarkt": {"domains": ["transfermarkt.com"], "type": "transfer_database"},
    # Reference
    "wikipedia": {"domains": ["wikipedia.org"], "type": "encyclopedia"},
    "stackoverflow": {"domains": ["stackoverflow.com"], "type": "qa_forum"},
}

# Subject domain detection keywords
_DOMAIN_KEYWORDS = {
    "tech": [
        "api", "sdk", "library", "framework", "programming", "code", "developer",
        "software", "hardware", "ai", "machine learning", "llm", "gpt", "model",
        "server", "database", "cloud", "deploy", "docker", "kubernetes", "git",
        "bug", "error", "fix", "install", "config", "endpoint", "webhook",
    ],
    "news": [
        "news", "breaking", "announce", "election", "president", "government",
        "policy", "economy", "market", "stock", "trade", "war", "conflict",
        "climate", "protest", "summit", "deal", "agreement", "crisis",
    ],
    "sports": [
        "score", "match", "game", "fixture", "transfer", "league", "champion",
        "tournament", "player", "team", "coach", "season", "standings", "table",
        "premier league", "champions league", "nba", "nfl", "mlb", "f1",
    ],
    "science": [
        "study", "research", "paper", "discovery", "experiment", "hypothesis",
        "clinical trial", "drug", "treatment", "disease", "vaccine", "genome",
        "physics", "chemistry", "biology", "space", "nasa", "telescope",
    ],
    "media": [
        "movie", "film", "show", "series", "album", "song", "book", "game",
        "actor", "actress", "director", "release", "premiere", "season",
        "episode", "streaming", "netflix", "spotify", "playstation", "xbox",
        "trailer", "review", "rating", "oscar", "emmy", "grammy",
    ],
    "finance": [
        "bitcoin", "crypto", "cryptocurrency", "ethereum", "altcoin", "blockchain",
        "stock price", "share price", "stock market", "invest", "trading",
        "ticker", "nasdaq", "nyse", "forex", "cryptocurrency", "etf", "dividend",
        "index fund", "exchange rate", "interest rate", "yield", "financial",
        "stock", "bank", "economy", "inflation",
    ],
}


@dataclass
class SearchPlan:
    """Describes the search strategy for a given user query."""
    queries: list[str] = field(default_factory=list)
    target_domains: list[str] = field(default_factory=list)
    source_type: str = "general"
    domain: str = "general"
    needs_freshness: bool = False
    is_user_specified: bool = False
    intent_description: str = ""


def _detect_domain(message: str) -> str:
    """Detect the subject domain of a user message."""
    lower = message.lower()
    scores = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = 0
        for kw in keywords:
            # Word-boundary match to avoid substring false positives
            # (e.g. "ai" matching inside "affecting" or "actor" inside "factors")
            if re.search(r"\b" + re.escape(kw) + r"\b", lower):
                score += 1
        if score > 0:
            scores[domain] = score
    if not scores:
        return "general"
    return max(scores, key=scores.get)


def plan_search(message: str, directed_site: str = "", research_domain: str = "") -> SearchPlan:
    """Analyze a user message and generate a search strategy.

    This does NOT perform the search — it only plans what to search.

    Args:
        message: The user's research query
        directed_site: Optional site to constrain search to
        research_domain: Optional domain hint from classify_research_intent
    """
    plan = SearchPlan()

    text = (message or "").strip()
    lower = text.lower()

    # Use provided domain hint or detect from message
    plan.domain = research_domain or _detect_domain(text)

    # Check for time-sensitive keywords
    for kw in _FRESHNESS_KEYWORDS:
        if kw in lower:
            plan.needs_freshness = True
            break

    # Check for user-specified sources
    if directed_site:
        plan.is_user_specified = True
        plan.target_domains.append(directed_site)
        plan.queries.append(text)
        plan.source_type = "user_specified"
        plan.intent_description = f"User directed search to {directed_site}"
        return plan

    # Check for domain mentions in the message
    for keyword, strategy in _DOMAIN_STRATEGIES.items():
        if keyword in lower:
            plan.target_domains.extend(strategy["domains"])
            plan.source_type = strategy["type"]

    # Generate primary query — use the user's message as-is
    plan.queries.append(text)

    # Generate supplementary queries based on domain and question type
    from datetime import datetime as _dt
    _current_year = str(_dt.now().year)
    if plan.domain == "tech":
        if any(kw in lower for kw in ["error", "bug", "issue", "fix", "problem"]):
            clean = re.sub(r"\bmy\b", "the", text, flags=re.IGNORECASE)
            plan.queries.append(f"{clean} solution fix")
        if any(kw in lower for kw in ["api", "endpoint", "sdk", "library"]):
            plan.queries.append(f"official documentation {text}")
        if plan.needs_freshness:
            plan.queries.append(f"{text} {_current_year}")
    elif plan.domain == "news":
        plan.queries.append(f"{text} latest news")
        if plan.needs_freshness:
            plan.queries.append(f"{text} today")
    elif plan.domain == "sports":
        plan.queries.append(f"{text} scores results")
        plan.queries.append(f"{text} latest news")
    elif plan.domain == "science":
        plan.queries.append(f"{text} research study")
        plan.queries.append(f"{text} latest findings")
    elif plan.domain == "media":
        plan.queries.append(f"{text} reviews ratings")
        plan.queries.append(f"{text} cast information")
    elif plan.domain == "finance":
        # Prefer live market data: add a "live/current price" query plus news context
        plan.source_type = plan.source_type if plan.source_type != "general" else "financial"
        plan.queries.append(f"{text} live price today")
        if plan.needs_freshness:
            plan.queries.append(f"{text} current price {_dt.now().strftime('%B %d %Y')} {_current_year}")
    else:
        # General: add a freshness query if needed
        if plan.needs_freshness:
            plan.queries.append(f"{text} latest {_current_year}")

    # Limit to 3 queries max
    plan.queries = plan.queries[:3]

    plan.intent_description = (
        f"Domain: {plan.domain}, "
        f"Source type: {plan.source_type}, "
        f"Freshness needed: {plan.needs_freshness}, "
        f"Target domains: {plan.target_domains or 'any'}, "
        f"Queries: {len(plan.queries)}"
    )
    return plan


# ═══════════════════════════════════════════════════════════════════════════════
# Search → Open → Verify pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def _dedupe_results(results: list[dict]) -> list[dict]:
    """Deduplicate search results by domain + path (ignore query params).

    Strips tracking query params and fragments for cleaner deduplication.
    """
    seen = set()
    deduped = []
    for r in results:
        url = r.get("url", "")
        try:
            parsed = urlparse(url)
            key = f"{parsed.netloc}{parsed.path}".rstrip("/").lower()
        except Exception:
            key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _parse_published_date(published: str) -> Optional[datetime]:
    """Best-effort parse of a publication date string into a datetime.

    Returns None if it cannot be parsed.  Handles common formats:
      2026-08-26, 2026-08-26T12:34:56, Aug 26, 2026, August 26, 2026,
      26 Aug 2026, 2026/08/26.
    """
    if not published:
        return None
    text = str(published).strip()
    if not text:
        return None

    # ISO date formats
    iso_match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso_match:
        try:
            return datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None

    # YYYY/MM/DD
    slash_match = re.match(r"(\d{4})[/](\d{1,2})[/](\d{1,2})", text)
    if slash_match:
        try:
            return datetime(int(slash_match.group(1)), int(slash_match.group(2)), int(slash_match.group(3)))
        except ValueError:
            return None

    # "Aug 26, 2026" or "August 26, 2026" or "26 Aug 2026"
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m_re = re.compile(
        r"(?P<mon>[A-Za-z]{3,9})\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})"
    )
    m = m_re.search(text)
    if m:
        mon = months.get(m.group("mon").lower()[:3])
        if mon:
            try:
                return datetime(int(m.group("year")), mon, int(m.group("day")))
            except ValueError:
                return None

    m_re2 = re.compile(
        r"(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3,9})\s+(?P<year>\d{4})"
    )
    m2 = m_re2.search(text)
    if m2:
        mon = months.get(m2.group("mon").lower()[:3])
        if mon:
            try:
                return datetime(int(m2.group("year")), mon, int(m2.group("day")))
            except ValueError:
                return None

    return None


def _age_label(published: str, now: Optional[datetime] = None) -> tuple[Optional[float], str]:
    """Compute age (in days) and a freshness label for a published date.

    Returns (age_days_or_None, label).  label is one of:
      "today", "yesterday", "recent" (< 7 days), "old" (>= 7 days),
      "" (unparseable/no date).
    """
    dt = _parse_published_date(published)
    if dt is None:
        return None, ""
    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    published_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    age_days = (today - published_day).days
    if age_days <= 0:
        label = "today"
    elif age_days == 1:
        label = "yesterday"
    elif age_days < 7:
        label = "recent"
    else:
        label = "old"
    return float(age_days), label


def _rank_results(results: list[dict], plan: SearchPlan) -> list[dict]:
    """Rank search results by relevance to the search plan."""
    now = datetime.now()
    for r in results:
        score = 0.0
        domain = (r.get("domain") or "").lower().replace("www.", "")
        title = (r.get("title") or "").lower()
        snippet = (r.get("snippet") or "").lower()

        # Tier bonus
        source = classify_domain(domain)
        tier_bonus = {1: 0.4, 2: 0.25, 3: 0.1, 4: 0.0}.get(source.tier, 0.0)
        score += tier_bonus

        # Target domain bonus
        if plan.target_domains:
            for td in plan.target_domains:
                if td.lower() in domain:
                    score += 0.3
                    break

        # Keyword relevance (title match > snippet match)
        query_words = set(re.findall(r"\w{3,}", plan.queries[0].lower()))
        title_words = set(re.findall(r"\w{3,}", title))
        snippet_words = set(re.findall(r"\w{3,}", snippet))

        title_overlap = len(query_words & title_words) / max(len(query_words), 1)
        snippet_overlap = len(query_words & snippet_words) / max(len(query_words), 1)
        score += title_overlap * 0.2
        score += snippet_overlap * 0.1

        # Official documentation bonus
        if plan.source_type == "official_documentation" and source.tier == 1:
            score += 0.2

        # Market-data / exchange bonus for financial or freshness queries
        # (for prices: an exchange/market snapshot is better than a prediction site)
        if plan.domain == "finance" or plan.needs_freshness:
            source_type = (source.source_type or "").lower()
            if source_type in ("exchange_market_data", "market_data"):
                score += 0.3
            elif source_type in ("financial_news", "news_agency"):
                score += 0.15
            # Penalize prediction/forecast/sentiment sites for price queries
            tl = (r.get("title") or "") + " " + (r.get("snippet") or "")
            tl_lower = tl.lower()
            if any(w in tl_lower for w in ["prediction", "forecast", "price prediction",
                                           "predict", "sentiment", "will bitcoin reach"]):
                if plan.domain == "finance":
                    score -= 0.3

        # ── Freshness scoring (graded by age) ──────────────────────
        published = (r.get("published") or "").strip()
        age_days, age_label = _age_label(published, now)
        r["_age_days"] = age_days
        r["_age_label"] = age_label

        if plan.needs_freshness:
            if age_label == "today":
                score += 0.5
            elif age_label == "yesterday":
                score += 0.25
            elif age_label == "recent":
                score += 0.1
            elif published:
                # has a date but is old → no boost, slight penalty
                score -= 0.1
            else:
                # No parseable date on a freshness query → mild penalty
                # (could be stale or untimestamped; not preferred)
                score -= 0.05
            # Bonus for fresh authoritative sources
            if age_label in ("today", "yesterday"):
                if source.tier <= 2:
                    score += 0.2
                if source.tier == 1:
                    score += 0.15

        r["_score"] = round(score, 4)
        r["_source"] = source

    results.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return results


def _build_evidence_from_snippet(result: dict) -> Evidence:
    """Build an Evidence object from a search result snippet."""
    source = result.get("_source") or classify_domain(result.get("domain", ""))
    snippet = result.get("snippet", "")
    title = result.get("title", "")
    url = result.get("url", "")

    # Extract the most relevant content (snippet is already truncated by search API)
    content = snippet or title

    published = str(result.get("published", "") or "")
    _age, age_label = _age_label(published)

    return Evidence(
        source_name=source.name,
        source_domain=source.domain,
        source_icon=source.icon_url,
        source_type=source.source_type,
        url=url,
        title=title,
        content=content[:800],
        relevance=result.get("_score", 0.0),
        supports_claims=_extract_claims(content),
        published=published,
        accessed_at=datetime.now(timezone.utc).isoformat(),
        tier=source.tier,
        freshness_label=age_label,
    )


def _extract_claims(text: str) -> list[str]:
    """Extract key claims/facts from a text passage."""
    claims = []
    # Look for specific facts: numbers with context, dates, limits, prices
    # Use word-boundary-safe patterns to avoid matching digits inside URLs/ids
    number_patterns = [
        re.compile(r"\b(\d[\d,]*\.?\d*)\s+(?:tokens?|tpm|rpm|requests?|per\s+minute|limit|max)\b"),
        re.compile(r"\b(?:limit|max|cap(?:acity)?)\s*(?:is|=|:)\s*(\d[\d,]*\.?\d*)\b"),
        re.compile(r"\$\s*[\d,.]+\b"),
        re.compile(r"\b(?:free|paid|tier|plan)\s+(?:tier|plan|limit|level)\b"),
    ]
    for pat in number_patterns:
        matches = pat.findall(text.lower())
        for m in matches[:2]:
            if isinstance(m, str) and len(m) > 1:
                claims.append(m.strip())

    # General fact extraction: dates, named entities, key statements
    if not claims:
        # Extract sentences that contain factual indicators
        sentences = re.split(r"[.!?\n]", text)
        fact_indicators = re.compile(
            r"\b(?:according to|announced|launched|released|confirmed|reported|"
            r"study found|research shows|data reveals|evidence suggests|"
            r"broke|won|lost|signed|acquired|merged|filed|signed into law|"
            r"directed by|starring|premiered|aired|rated|scored)\b",
            re.IGNORECASE,
        )
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 20 or len(sent) > 300:
                continue
            if fact_indicators.search(sent):
                claims.append(sent[:200])
                if len(claims) >= 3:
                    break

    # Fallback: use the first meaningful sentence
    if not claims and len(text) > 30:
        first_sent = re.split(r"[.!?\n]", text)[0].strip()
        if first_sent and len(first_sent) > 20:
            claims.append(first_sent[:200])
    return claims[:3]


def _detect_conflicts(evidence: list[Evidence]) -> list[str]:
    """Detect conflicting claims across evidence items.

    Returns a list of conflict descriptions (empty if no conflicts).
    Only flags true conflicts: same subject + same attribute but different values.
    Uses word-boundary-safe number extraction and validates that conflicting
    claims share enough context to actually be about the same thing.
    """
    if len(evidence) < 2:
        return []

    conflicts = []
    # Group claims by source domain
    source_claims: dict[str, list[str]] = {}
    for ev in evidence:
        domain = ev.source_domain
        if domain not in source_claims:
            source_claims[domain] = []
        for claim in ev.supports_claims:
            source_claims[domain].append(claim.lower().strip())

    # Common "attribute" keywords that indicate what property is being described
    _attribute_words = re.compile(
        r"(?:tokens?|tpm|rpm|requests?|limit|max|cap|price|cost|rate|"
        r"users?|revenue|growth|speed|score|rating|population|"
        r"percentage|share|margin|return|yield|rate|frequency|count|"
        r"level|volume|distance|weight|size|area|height|width|depth)",
        re.IGNORECASE,
    )

    # Compare across different domains
    domains = list(source_claims.keys())
    for i in range(len(domains)):
        for j in range(i + 1, len(domains)):
            d1, d2 = domains[i], domains[j]
            for c1 in source_claims[d1]:
                for c2 in source_claims[d2]:
                    # Require minimum word overlap to be about the same topic
                    words1 = set(re.findall(r"\b\w{3,}\b", c1))
                    words2 = set(re.findall(r"\b\w{3,}\b", c2))
                    overlap = words1 & words2
                    if len(overlap) < 3:
                        continue

                    # Extract numbers with word-boundary safety
                    nums1 = set(re.findall(r"\b(\d[\d,]*\.?\d*)\b", c1))
                    nums2 = set(re.findall(r"\b(\d[\d,]*\.?\d*)\b", c2))
                    if not nums1 or not nums2 or nums1 == nums2:
                        continue

                    # Check both claims mention an attribute keyword
                    # This avoids flagging unrelated sentences that happen to share numbers
                    attr1 = _attribute_words.search(c1)
                    attr2 = _attribute_words.search(c2)
                    if attr1 and attr2:
                        # Same attribute keyword → real conflict
                        conflicts.append(
                            f"Sources disagree: {d1} says '{c1[:80]}' "
                            f"but {d2} says '{c2[:80]}'"
                        )
                    elif len(overlap) >= 5:
                        # Many shared words but no attribute keyword match
                        # Only flag if very high overlap
                        conflicts.append(
                            f"Sources disagree: {d1} says '{c1[:80]}' "
                            f"but {d2} says '{c2[:80]}'"
                        )
                    if len(conflicts) >= 3:
                        return conflicts
    return conflicts


# ═══════════════════════════════════════════════════════════════════════════════
# Research confidence scoring
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_confidence(evidence: list[Evidence], total_searched: int) -> str:
    """Compute research confidence based on evidence quality and diversity.

    Factors:
    - Tier 1 (Wikipedia, gov) and Tier 2 (major outlets) sources
    - Number of independent sources (domains)
    - Total evidence count
    """
    if not evidence:
        return "low"

    tier1_count = sum(1 for e in evidence if e.tier == 1)
    tier2_count = sum(1 for e in evidence if e.tier == 2)
    # Use source_name as fallback when domain is empty (unit tests)
    unique_sources = len({
        e.source_domain or e.source_name for e in evidence
    })
    total = len(evidence)

    # High: multiple strong sources agreeing
    if tier1_count >= 2:
        return "high"
    if tier1_count >= 1 and tier2_count >= 2 and unique_sources >= 3:
        return "high"
    if tier2_count >= 3 and unique_sources >= 3:
        return "high"

    # Medium: at least some quality evidence
    if tier1_count >= 1 and tier2_count >= 1:
        return "medium"
    if tier2_count >= 2 and unique_sources >= 2:
        return "medium"
    if tier1_count >= 1:
        return "medium"
    if tier2_count >= 1:
        return "medium"

    # Low: only weak sources or very few results
    return "low"


# ═══════════════════════════════════════════════════════════════════════════════
# Main research orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class SourceIntelligence:
    """High-level interface for source research and evidence extraction.

    Usage::

        si = SourceIntelligence()
        package = si.research_with_fetch(
            "What are Groq's current rate limits?",
            directed_site="console.groq.com",
        )
        if package.is_research_request:
            context_str = package.to_context_string()
            frontend_meta = package.to_frontend_metadata()
    """

    def __init__(self):
        self._lock = threading.Lock()

    def research_with_fetch(
        self,
        message: str,
        directed_site: str = "",
        research_domain: str = "",
    ) -> EvidencePackage:
        """Full research pipeline with multi-query search and page content fetching.

        This is the Phase 1 research engine:
        1. Plans multi-query search strategy
        2. Executes up to 3 search queries
        3. Fetches full page content from top results
        4. Extracts claims from page content
        5. Cross-checks sources for conflicts
        6. Computes confidence with conflict awareness
        7. Returns enriched EvidencePackage with page content
        """
        from core.external_apis import (
            _search_general_web,
            _fetch_url_content,
            get_last_structured_results,
            _reset_search_sources,
            LIVE_DATA_UNAVAILABLE,
        )

        log: list[str] = []
        package = EvidencePackage(research_log=log, is_research_request=True)

        # Step 1: Plan multi-query search
        plan = plan_search(message, directed_site=directed_site, research_domain=research_domain)
        log.append(f"Search plan: {plan.intent_description}")

        # Determine freshness category based on plan signals
        if plan.needs_freshness or plan.domain in ("news", "sports", "finance"):
            package.freshness_category = "current"
        else:
            package.freshness_category = "background"
        print(f"[SOURCE INTEL] Plan: {plan.intent_description}")

        # Step 2: Execute searches (up to 3 queries)
        all_results: list[dict] = []
        total_searched = 0

        for query in plan.queries[:3]:
            try:
                _reset_search_sources()
                raw = _search_general_web(query, site=directed_site or "")
                if raw and raw != LIVE_DATA_UNAVAILABLE:
                    structured = get_last_structured_results()
                    if structured:
                        results = structured
                    else:
                        results = _parse_search_text(raw)
                    all_results.extend(results)
                    total_searched += len(results)
                    log.append(f"Search '{query[:60]}': {len(results)} results")
                else:
                    log.append(f"Search '{query[:60]}': no results")
            except Exception as exc:
                log.append(f"Search '{query[:60]}': failed ({exc})")
                print(f"[SOURCE INTEL] Search failed: {exc}")

        if not all_results:
            package.total_searched = total_searched
            package.confidence = "low"
            log.append("No search results found")
            return package

        # Step 3: Deduplicate and rank
        deduped = _dedupe_results(all_results)
        log.append(f"After dedup: {len(deduped)} unique sources")
        ranked = _rank_results(deduped, plan)

        # Step 4: Fetch page content from top results (up to 3)
        fetched_urls = set()
        for r in ranked[:5]:
            url = r.get("url", "")
            if not url or url in fetched_urls:
                continue
            fetched_urls.add(url)
            try:
                content = _fetch_url_content(url, max_chars=MAX_FETCH_CHARS)
                if content:
                    r["_fetched_content"] = content
                    log.append(f"Fetched {urlparse(url).netloc}: {len(content)} chars")
            except Exception as exc:
                log.append(f"Fetch {urlparse(url).netloc} failed: {exc}")

        # Step 5: Build evidence (prefer fetched content over snippets)
        evidence = []
        for r in ranked[:EVIDENCE_BUDGET_EVIDENCES * 2]:
            if len(evidence) >= EVIDENCE_BUDGET_EVIDENCES:
                break
            fetched = r.get("_fetched_content", "")
            if fetched:
                # Use fetched content for richer evidence
                source = r.get("_source") or classify_domain(r.get("domain", ""))
                claims = _extract_claims(fetched)
                published = str(r.get("published", "") or "")
                _age, age_label = _age_label(published)
                ev = Evidence(
                    source_name=source.name,
                    source_domain=source.domain,
                    source_icon=source.icon_url,
                    source_type=source.source_type,
                    url=r.get("url", ""),
                    title=r.get("title", ""),
                    content=fetched[:800],
                    relevance=r.get("_score", 0.0),
                    supports_claims=claims,
                    published=published,
                    accessed_at=datetime.now(timezone.utc).isoformat(),
                    tier=source.tier,
                    freshness_label=age_label,
                )
                if ev.content and ev.url:
                    evidence.append(ev)
            else:
                ev = _build_evidence_from_snippet(r)
                if ev.content and ev.url:
                    evidence.append(ev)

        # Step 6: Detect conflicts
        conflicts = _detect_conflicts(evidence)
        if conflicts:
            log.append(f"Conflicts detected: {len(conflicts)}")
            for c in conflicts:
                log.append(f"  Conflict: {c}")

        # Step 7: Compute confidence (conflict-aware)
        confidence = _compute_confidence(evidence, total_searched)
        if conflicts and confidence == "high":
            confidence = "medium"
            log.append("Downgraded confidence due to conflicts")

        package.evidence = evidence
        package.total_searched = total_searched
        package.total_opened = len(fetched_urls)
        package.total_used = len(evidence)
        package.confidence = confidence
        package.conflicts = conflicts

        log.append(f"Evidence: {len(evidence)} items, confidence={confidence}, "
                   f"fetched: {len(fetched_urls)}, conflicts: {len(conflicts)}")
        print(f"[SOURCE INTEL] Research complete: {len(evidence)} evidence items, "
              f"confidence={confidence}, searched={total_searched}, "
              f"fetched={len(fetched_urls)}, conflicts={len(conflicts)}")

        return package

    def build_evidence_package(
        self,
        search_text: str,
        sources: list[dict],
        query: str = "",
    ) -> EvidencePackage:
        """Build an EvidencePackage from already-gathered search results.

        This is a lightweight, offline method that does NOT make any network
        calls.  It takes the formatted search text (as returned by
        ``_search_general_web``) and an optional list of source dicts
        (``{title, url, domain}``) and produces an ``EvidencePackage`` suitable
        for passing to any LLM provider.

        Use this when search has already happened and you just need to package
        the results for the LLM context window or for frontend metadata.
        """
        package = EvidencePackage(
            is_research_request=bool(search_text or sources),
            research_log=[],
        )

        # Merge parsed results from search text with explicit sources
        parsed = _parse_search_text(search_text) if search_text else []
        merged = list(sources) if sources else []
        seen_urls = {s.get("url") for s in merged}
        for p in parsed:
            url = p.get("url", "")
            if url and url not in seen_urls:
                merged.append(p)
                seen_urls.add(url)

        if not merged:
            package.confidence = "low"
            return package

        # Rank the merged results against the query
        plan = plan_search(query) if query else SearchPlan(queries=[query or ""])
        ranked = _rank_results(merged, plan)

        # Build evidence from each result
        evidence: list[Evidence] = []
        for r in ranked[:EVIDENCE_BUDGET_EVIDENCES]:
            ev = _build_evidence_from_snippet(r)
            if ev.content or ev.title:
                evidence.append(ev)

        # Detect conflicts and compute confidence
        conflicts = _detect_conflicts(evidence)
        confidence = _compute_confidence(evidence, len(merged))
        if conflicts and confidence == "high":
            confidence = "medium"

        package.evidence = evidence
        package.total_searched = len(merged)
        package.total_opened = len(merged)
        package.total_used = len(evidence)
        package.confidence = confidence
        package.conflicts = conflicts
        return package


# ═══════════════════════════════════════════════════════════════════════════════
# Search text parsing (existing pipeline output → structured data)
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_search_text(text: str) -> list[dict]:
    """Parse the formatted search text from _search_general_web into dicts.

    The existing pipeline returns text like:
        Live search results:
        - Title: Summary text
        - Title2: Summary2

    We parse this back into structured data.
    """
    if not text:
        return []

    results = []
    lines = text.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line.startswith("- "):
            continue
        line = line[2:]

        # Try to split "Title: Summary"
        parts = line.split(": ", 1)
        if len(parts) == 2:
            title = parts[0].strip()
            snippet = parts[1].strip()
        else:
            title = line.strip()
            snippet = ""

        # Try to extract domain from title
        domain = ""
        url = ""
        url_match = re.search(r"https?://[^\s]+", title)
        if url_match:
            url = url_match.group(0)
            try:
                domain = urlparse(url).netloc.replace("www.", "")
            except Exception:
                pass

        if not domain:
            # Try to guess domain from title
            domain_match = re.search(
                r"(?:from|on|at|via|source:?)\s+([a-z0-9][a-z0-9.\-]*\.[a-z]{2,})",
                title, re.IGNORECASE,
            )
            if domain_match:
                domain = domain_match.group(1)

        if not url and domain:
            url = f"https://{domain}"

        results.append({
            "title": title[:200],
            "url": url,
            "domain": domain,
            "snippet": snippet[:400],
            "published": "",
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Research status messages (for streaming to frontend)
# ═══════════════════════════════════════════════════════════════════════════════

def research_status_message(phase: str, detail: str = "") -> str:
    """Generate a user-facing research status message."""
    statuses = {
        "planning": "Analyzing your question…",
        "searching": "Searching for information…",
        "searching_official": "Checking official documentation…",
        "searching_general": "Searching the web…",
        "verifying": "Verifying sources…",
        "synthesizing": "Synthesizing answer…",
        "complete": "Research complete.",
        "failed": "Could not find reliable sources.",
    }
    return statuses.get(phase, detail or "Researching…")
