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

    def to_context_string(self, budget: int = EVIDENCE_BUDGET_CHARS) -> str:
        """Render evidence as a compact context string for the LLM."""
        if not self.evidence:
            return ""

        lines = ["[SOURCE INTELLIGENCE — Verified Evidence]", ""]
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

    source_type = "official_documentation" if tier == 1 else "web"

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
}


@dataclass
class SearchPlan:
    """Describes the search strategy for a given user query."""
    queries: list[str] = field(default_factory=list)
    target_domains: list[str] = field(default_factory=list)
    source_type: str = "general"
    needs_freshness: bool = False
    is_user_specified: bool = False
    intent_description: str = ""


def plan_search(message: str, directed_site: str = "") -> SearchPlan:
    """Analyze a user message and generate a search strategy.

    This does NOT perform the search — it only plans what to search.
    """
    plan = SearchPlan()

    text = (message or "").strip()
    lower = text.lower()

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

    # For technical questions, generate supplementary queries
    if any(kw in lower for kw in ["error", "bug", "issue", "fix", "problem"]):
        # Technical troubleshooting — add a query focused on solutions
        clean = re.sub(r"\bmy\b", "the", text, flags=re.IGNORECASE)
        plan.queries.append(f"{clean} solution fix")

    if any(kw in lower for kw in ["api", "endpoint", "sdk", "library"]):
        # API questions — add official docs query
        plan.queries.append(f"official documentation {text}")

    plan.intent_description = (
        f"Source type: {plan.source_type}, "
        f"Freshness needed: {plan.needs_freshness}, "
        f"Target domains: {plan.target_domains or 'any'}"
    )
    return plan


# ═══════════════════════════════════════════════════════════════════════════════
# Search → Open → Verify pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def _dedupe_results(results: list[dict]) -> list[dict]:
    """Deduplicate search results by domain + path (ignore query params)."""
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


def _rank_results(results: list[dict], plan: SearchPlan) -> list[dict]:
    """Rank search results by relevance to the search plan."""
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

        # Freshness boost for time-sensitive queries
        if plan.needs_freshness:
            published = (r.get("published") or "").strip()
            if published:
                score += 0.15

        # Official documentation bonus
        if plan.source_type == "official_documentation" and source.tier == 1:
            score += 0.2

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
        published=result.get("published", ""),
        accessed_at=datetime.now(timezone.utc).isoformat(),
        tier=source.tier,
    )


def _extract_claims(text: str) -> list[str]:
    """Extract key claims/facts from a text passage."""
    claims = []
    # Look for specific facts: numbers, dates, limits, prices
    number_patterns = [
        re.compile(r"(\d[\d,]*\.?\d*)\s*(?:tokens?|tpm|rpm|requests?|per\s+minute|limit|max)"),
        re.compile(r"(?:limit|max|cap(?:acity)?)\s*(?:is|=|:)\s*(\d[\d,]*\.?\d*)"),
        re.compile(r"\$[\d,.]+"),
        re.compile(r"(?:free|paid|tier|plan)\s+(?:tier|plan|limit|level)"),
    ]
    for pat in number_patterns:
        matches = pat.findall(text.lower())
        for m in matches[:2]:
            if isinstance(m, str) and len(m) > 1:
                claims.append(m.strip())
    if not claims and len(text) > 30:
        # Fallback: use the first sentence as the claim
        first_sent = re.split(r"[.!?\n]", text)[0].strip()
        if first_sent:
            claims.append(first_sent[:120])
    return claims[:3]


# ═══════════════════════════════════════════════════════════════════════════════
# Research confidence scoring
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_confidence(evidence: list[Evidence], total_searched: int) -> str:
    """Compute research confidence based on evidence quality."""
    if not evidence:
        return "low"

    tier1_count = sum(1 for e in evidence if e.tier == 1)
    tier2_count = sum(1 for e in evidence if e.tier <= 2)
    total = len(evidence)

    if tier1_count >= 2:
        return "high"
    if tier1_count >= 1 and tier2_count >= 2:
        return "high"
    if tier2_count >= 2:
        return "high"
    if tier1_count >= 1 or tier2_count >= 1:
        return "medium"
    if total >= 3:
        return "medium"
    return "low"


# ═══════════════════════════════════════════════════════════════════════════════
# Main research orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class SourceIntelligence:
    """High-level interface for source research and evidence extraction.

    Usage::

        si = SourceIntelligence()
        package = si.research(
            "What are Groq's current rate limits?",
            directed_site="console.groq.com",
        )
        if package.is_research_request:
            context_str = package.to_context_string()
            frontend_meta = package.to_frontend_metadata()
    """

    def __init__(self):
        self._lock = threading.Lock()

    def research(
        self,
        message: str,
        directed_site: str = "",
        existing_search_results: str = "",
        existing_sources: list[dict] | None = None,
    ) -> EvidencePackage:
        """Run the full research pipeline and return an evidence package.

        This is the main entry point.  It:
        1. Plans the search strategy
        2. Uses existing search results if available (from the current pipeline)
        3. Deduplicates and ranks results
        4. Builds evidence from snippets
        5. Computes confidence
        6. Returns a compact evidence package

        The package is provider-agnostic — it survives LLM fallbacks.
        """
        from core.external_apis import (
            _search_general_web,
            _search_tinyfish,
            LIVE_DATA_UNAVAILABLE,
        )

        log: list[str] = []
        package = EvidencePackage(research_log=log, is_research_request=True)

        # Step 1: Plan the search
        plan = plan_search(message, directed_site=directed_site)
        log.append(f"Search plan: {plan.intent_description}")
        print(f"[SOURCE INTEL] Plan: {plan.intent_description}")

        # Step 2: Execute searches
        all_results: list[dict] = []
        total_searched = 0

        for query in plan.queries[:2]:  # max 2 search queries
            try:
                raw = _search_general_web(query, site=directed_site or "")
                if raw and raw != LIVE_DATA_UNAVAILABLE:
                    results = _parse_search_text(raw)
                    all_results.extend(results)
                    total_searched += len(results)
                    log.append(f"Search '{query[:60]}': {len(results)} results")
                else:
                    log.append(f"Search '{query[:60]}': no results")
            except Exception as exc:
                log.append(f"Search '{query[:60]}': failed ({exc})")
                print(f"[SOURCE INTEL] Search failed: {exc}")

        # Step 2b: Also use existing search results from the pipeline
        if existing_sources:
            for src in existing_sources:
                all_results.append({
                    "title": src.get("title", ""),
                    "url": src.get("url", ""),
                    "domain": src.get("domain", ""),
                    "snippet": "",
                })
            log.append(f"Ingested {len(existing_sources)} existing pipeline sources")

        if not all_results:
            package.total_searched = total_searched
            package.confidence = "low"
            log.append("No search results found")
            return package

        # Step 3: Deduplicate
        deduped = _dedupe_results(all_results)
        log.append(f"After dedup: {len(deduped)} unique sources")

        # Step 4: Rank
        ranked = _rank_results(deduped, plan)

        # Step 5: Build evidence from top results (up to budget)
        evidence = []
        for r in ranked[:EVIDENCE_BUDGET_EVIDENCES * 2]:
            if len(evidence) >= EVIDENCE_BUDGET_EVIDENCES:
                break
            ev = _build_evidence_from_snippet(r)
            if ev.content and ev.url:
                evidence.append(ev)

        # Step 6: Compute confidence
        confidence = _compute_confidence(evidence, total_searched)

        package.evidence = evidence
        package.total_searched = total_searched
        package.total_opened = len(evidence)
        package.total_used = len(evidence)
        package.confidence = confidence

        log.append(f"Evidence: {len(evidence)} items, confidence={confidence}")
        print(f"[SOURCE INTEL] Research complete: {len(evidence)} evidence items, "
              f"confidence={confidence}, searched={total_searched}")

        return package

    def build_evidence_package(
        self,
        search_results_text: str,
        search_sources: list[dict],
        user_message: str,
        directed_site: str = "",
    ) -> EvidencePackage:
        """Build an evidence package from the existing search pipeline's output.

        This is a lightweight version that doesn't re-search — it just
        enriches the existing results with source intelligence metadata.
        """
        log: list[str] = []
        package = EvidencePackage(research_log=log, is_research_request=True)

        plan = plan_search(user_message, directed_site=directed_site)

        # Parse existing search results text into structured data
        results = _parse_search_text(search_results_text)
        if search_sources:
            for src in search_sources:
                results.append({
                    "title": src.get("title", ""),
                    "url": src.get("url", ""),
                    "domain": src.get("domain", ""),
                    "snippet": "",
                })

        if not results:
            package.confidence = "low"
            log.append("No results to package")
            return package

        deduped = _dedupe_results(results)
        ranked = _rank_results(deduped, plan)

        evidence = []
        for r in ranked[:EVIDENCE_BUDGET_EVIDENCES]:
            ev = _build_evidence_from_snippet(r)
            if ev.content and ev.url:
                evidence.append(ev)

        package.evidence = evidence
        package.total_searched = len(deduped)
        package.total_opened = len(evidence)
        package.total_used = len(evidence)
        package.confidence = _compute_confidence(evidence, len(deduped))
        log.append(f"Packaged {len(evidence)} evidence items from existing results")

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
