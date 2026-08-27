"""Tests for the Source Intelligence Layer.

Covers:
  - Source tier classification
  - Search query planning
  - Search result deduplication and ranking
  - Evidence extraction from snippets
  - Evidence package context string generation
  - Evidence package frontend metadata
  - Research confidence scoring
  - Source identity model
  - Existing memory system untouched
  - Search text parsing
  - Token budget enforcement
  - Provider-agnostic evidence packages
  - Favicon fallback

Run: env311\\Scripts\\python.exe -m unittest tests.test_source_intelligence -v
"""

from __future__ import annotations

import json
import re
import unittest

from core.source_intelligence import (
    SourceIdentity,
    SearchResult,
    Evidence,
    EvidencePackage,
    classify_domain,
    favicon_url,
    plan_search,
    SearchPlan,
    SourceIntelligence,
    _dedupe_results,
    _rank_results,
    _build_evidence_from_snippet,
    _extract_claims,
    _compute_confidence,
    _parse_search_text,
    _parse_published_date,
    _age_label,
    EVIDENCE_BUDGET_CHARS,
    EVIDENCE_BUDGET_EVIDENCES,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: Source tier classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourceTierClassification(unittest.TestCase):
    def test_official_groq(self):
        """Groq should be Tier 1 (official documentation)."""
        src = classify_domain("console.groq.com")
        assert src.tier == 1
        assert src.name == "Groq"

    def test_official_openai(self):
        """OpenAI should be Tier 1."""
        src = classify_domain("platform.openai.com")
        assert src.tier == 1
        assert src.name == "OpenAI"

    def test_official_github(self):
        """GitHub should be Tier 1."""
        src = classify_domain("github.com")
        assert src.tier == 1
        assert src.name == "GitHub"

    def test_reputable_stackoverflow(self):
        """Stack Overflow should be Tier 2."""
        src = classify_domain("stackoverflow.com")
        assert src.tier == 2
        assert src.name == "Stack Overflow"

    def test_reputable_arxiv(self):
        """arXiv should be Tier 2."""
        src = classify_domain("arxiv.org")
        assert src.tier == 2
        assert src.name == "arXiv"

    def test_unknown_domain(self):
        """Unknown domains should default to Tier 3."""
        src = classify_domain("some-random-blog.com")
        assert src.tier == 3
        assert "some" in src.name.lower()

    def test_tier4_reddit(self):
        """Reddit should be Tier 4."""
        src = classify_domain("reddit.com")
        assert src.tier == 4

    def test_tier4_twitter(self):
        """Twitter/X should be Tier 4."""
        src = classify_domain("x.com")
        assert src.tier == 4

    def test_favicon_url_generation(self):
        """Favicon URL should be generated for any domain."""
        url = favicon_url("example.com")
        assert "favicons" in url
        assert "example.com" in url

    def test_source_identity_to_dict(self):
        """SourceIdentity should serialize to dict correctly."""
        src = classify_domain("github.com")
        d = src.to_dict()
        assert d["source_id"] == "github.com"
        assert d["tier"] == 1
        assert "icon_url" in d


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: Search query planning
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchPlanning(unittest.TestCase):
    def test_basic_query(self):
        """A basic question should produce a simple plan."""
        plan = plan_search("What is Python?")
        assert len(plan.queries) >= 1
        assert plan.needs_freshness is False

    def test_time_sensitive_query(self):
        """Time-sensitive keywords should trigger freshness."""
        plan = plan_search("What are Groq's current rate limits?")
        assert plan.needs_freshness is True

    def test_directed_site(self):
        """User-specified site should set target domains."""
        plan = plan_search("search Groq for rate limits", directed_site="console.groq.com")
        assert plan.is_user_specified is True
        assert "console.groq.com" in plan.target_domains

    def test_domain_awareness(self):
        """Mentioning 'Groq' should target Groq domains."""
        plan = plan_search("What is the Groq API limit?")
        assert any("groq" in d for d in plan.target_domains)

    def test_error_query_supplements(self):
        """Error/bug queries should generate supplementary queries."""
        plan = plan_search("Why is my Groq request returning HTTP 413?")
        assert len(plan.queries) >= 1

    def test_api_query_supplements(self):
        """API queries should supplement with official docs query."""
        plan = plan_search("Does this endpoint support max_completion_tokens?")
        assert len(plan.queries) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: Search result deduplication
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeduplication(unittest.TestCase):
    def test_dedup_same_url(self):
        """Duplicate URLs should be removed."""
        results = [
            {"url": "https://groq.com/docs", "title": "A"},
            {"url": "https://groq.com/docs", "title": "B"},
            {"url": "https://groq.com/docs?q=1", "title": "C"},
        ]
        deduped = _dedupe_results(results)
        assert len(deduped) == 1

    def test_dedup_different_paths(self):
        """Different paths should be kept."""
        results = [
            {"url": "https://groq.com/docs", "title": "A"},
            {"url": "https://groq.com/pricing", "title": "B"},
        ]
        deduped = _dedupe_results(results)
        assert len(deduped) == 2

    def test_dedup_empty(self):
        """Empty list should produce empty list."""
        assert _dedupe_results([]) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: Search result ranking
# ═══════════════════════════════════════════════════════════════════════════════

class TestRanking(unittest.TestCase):
    def test_tier1_ranks_higher(self):
        """Tier 1 sources should rank higher than Tier 3."""
        plan = plan_search("What are Groq rate limits?")
        results = [
            {"url": "https://some-blog.com/groq-limits", "title": "Groq limits blog", "domain": "some-blog.com", "snippet": "Groq rate limits are X"},
            {"url": "https://console.groq.com/docs", "title": "Groq Rate Limits", "domain": "console.groq.com", "snippet": "Groq rate limits documentation"},
        ]
        ranked = _rank_results(results, plan)
        assert ranked[0]["domain"] == "console.groq.com"

    def test_target_domain_boost(self):
        """Results from target domains should be boosted."""
        plan = SearchPlan(
            queries=["rate limits"],
            target_domains=["console.groq.com"],
            source_type="official_documentation",
        )
        results = [
            {"url": "https://blog.com/limits", "title": "Limits", "domain": "blog.com", "snippet": "limits"},
            {"url": "https://console.groq.com/docs", "title": "Limits", "domain": "console.groq.com", "snippet": "limits"},
        ]
        ranked = _rank_results(results, plan)
        assert ranked[0]["domain"] == "console.groq.com"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: Evidence extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidenceExtraction(unittest.TestCase):
    def test_build_from_snippet(self):
        """Should build an Evidence from a search result."""
        result = {
            "title": "Groq Rate Limits",
            "url": "https://console.groq.com/docs",
            "domain": "console.groq.com",
            "snippet": "TPM limit is 8000 for free tier",
            "_score": 0.8,
            "_source": classify_domain("console.groq.com"),
        }
        ev = _build_evidence_from_snippet(result)
        assert ev.source_name == "Groq"
        assert ev.url == "https://console.groq.com/docs"
        assert "8000" in ev.content or "TPM" in ev.content
        assert ev.tier == 1

    def test_empty_snippet(self):
        """Should handle empty snippet gracefully."""
        result = {
            "title": "Test",
            "url": "https://example.com",
            "domain": "example.com",
            "snippet": "",
            "_score": 0.1,
            "_source": classify_domain("example.com"),
        }
        ev = _build_evidence_from_snippet(result)
        assert ev.content == "" or ev.content == "Test"

    def test_extract_claims_numbers(self):
        """Should extract numeric claims from text."""
        claims = _extract_claims("The TPM limit is 8000 tokens per minute")
        assert len(claims) >= 1

    def test_extract_claims_empty(self):
        """Empty text should produce no claims."""
        claims = _extract_claims("")
        assert claims == []


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6: Evidence package context string
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidencePackageContext(unittest.TestCase):
    def test_empty_package(self):
        """Empty package should produce empty string."""
        pkg = EvidencePackage()
        assert pkg.to_context_string() == ""

    def test_single_evidence(self):
        """Single evidence should produce formatted context."""
        pkg = EvidencePackage(
            evidence=[
                Evidence(
                    source_name="Groq",
                    source_domain="console.groq.com",
                    url="https://console.groq.com/docs",
                    title="Rate Limits",
                    content="TPM limit is 8000",
                    tier=1,
                ),
            ],
            total_searched=5,
            total_opened=3,
            total_used=1,
            confidence="high",
        )
        ctx = pkg.to_context_string()
        assert "SOURCE INTELLIGENCE" in ctx
        assert "Groq" in ctx
        assert "8000" in ctx
        assert "high" in ctx.lower()

    def test_budget_enforcement(self):
        """Context should respect the character budget."""
        evidences = []
        for i in range(20):
            evidences.append(Evidence(
                source_name=f"Source {i}",
                source_domain=f"source{i}.com",
                url=f"https://source{i}.com/page",
                title=f"Page {i}",
                content="x" * 500,
                tier=3,
            ))
        pkg = EvidencePackage(evidence=evidences, confidence="medium")
        ctx = pkg.to_context_string(budget=1000)
        assert len(ctx) <= 1200  # budget + some overhead for headers


# ═══════════════════════════════════════════════════════════════════════════════
# Test 7: Evidence package frontend metadata
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidencePackageFrontend(unittest.TestCase):
    def test_frontend_metadata_deduped(self):
        """Same domain should appear only once in frontend metadata."""
        pkg = EvidencePackage(
            evidence=[
                Evidence(source_name="Groq", source_domain="console.groq.com",
                        url="https://console.groq.com/docs", title="Docs"),
                Evidence(source_name="Groq", source_domain="console.groq.com",
                        url="https://console.groq.com/pricing", title="Pricing"),
                Evidence(source_name="OpenAI", source_domain="platform.openai.com",
                        url="https://platform.openai.com/docs", title="Docs"),
            ],
        )
        meta = pkg.to_frontend_metadata()
        domains = [m["domain"] for m in meta]
        assert domains.count("console.groq.com") == 1
        assert len(meta) == 2

    def test_frontend_metadata_structure(self):
        """Frontend metadata should have required fields."""
        pkg = EvidencePackage(
            evidence=[
                Evidence(source_name="GitHub", source_domain="github.com",
                        url="https://github.com/repo", title="Repo",
                        source_type="code_repository", tier=1),
            ],
        )
        meta = pkg.to_frontend_metadata()
        assert len(meta) == 1
        m = meta[0]
        assert "id" in m
        assert "name" in m
        assert "url" in m
        assert "domain" in m
        assert "icon" in m
        assert m["tier"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Test 8: Research confidence scoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfidenceScoring(unittest.TestCase):
    def test_high_confidence_tier1(self):
        """Multiple Tier 1 sources should yield high confidence."""
        evidence = [
            Evidence(source_name="Groq", tier=1),
            Evidence(source_name="OpenAI", tier=1),
        ]
        assert _compute_confidence(evidence, 10) == "high"

    def test_medium_confidence_tier2(self):
        """Single Tier 2 source should yield medium confidence."""
        evidence = [
            Evidence(source_name="StackOverflow", tier=2),
        ]
        assert _compute_confidence(evidence, 10) == "medium"

    def test_low_confidence_no_evidence(self):
        """No evidence should yield low confidence."""
        assert _compute_confidence([], 0) == "low"

    def test_low_confidence_single_tier4(self):
        """Single Tier 4 source should yield low confidence."""
        evidence = [Evidence(source_name="Reddit", tier=4)]
        assert _compute_confidence(evidence, 5) == "low"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 9: Existing memory system untouched
# ═══════════════════════════════════════════════════════════════════════════════

class TestExistingMemoryUntouched(unittest.TestCase):
    def test_memory_system_not_modified(self):
        """The Source Intelligence Layer should not modify MemorySystem."""
        from core.memory import MemorySystem
        import inspect
        src = inspect.getsource(MemorySystem)
        assert "SourceIntelligence" not in src
        assert "evidence_package" not in src
        assert "EvidencePackage" not in src

    def test_memory_manager_not_modified(self):
        """The Source Intelligence Layer should not modify MemoryManager."""
        from core.memory_manager import MemoryManager
        import inspect
        src = inspect.getsource(MemoryManager)
        assert "SourceIntelligence" not in src
        assert "evidence_package" not in src


# ═══════════════════════════════════════════════════════════════════════════════
# Test 10: Search text parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchTextParsing(unittest.TestCase):
    def test_parse_format_items(self):
        """Should parse the _format_items output format."""
        text = (
            "Live search results:\n"
            "- Groq Rate Limits: The official documentation for rate limits.\n"
            "- OpenAI API Reference: OpenAI's API documentation.\n"
        )
        results = _parse_search_text(text)
        assert len(results) == 2
        assert results[0]["title"] == "Groq Rate Limits"
        assert "rate limits" in results[0]["snippet"].lower()

    def test_parse_empty(self):
        """Empty text should produce empty list."""
        assert _parse_search_text("") == []
        assert _parse_search_text(None) == []

    def test_parse_no_items(self):
        """Text without bullet points should produce empty list."""
        assert _parse_search_text("Just some random text without bullets") == []


# ═══════════════════════════════════════════════════════════════════════════════
# Test 11: Provider-agnostic evidence package
# ═══════════════════════════════════════════════════════════════════════════════

class TestProviderAgnostic(unittest.TestCase):
    def test_evidence_package_serializable(self):
        """EvidencePackage should be JSON-serializable for fallback transfer."""
        pkg = EvidencePackage(
            evidence=[
                Evidence(source_name="Groq", source_domain="console.groq.com",
                        url="https://groq.com/docs", title="Docs",
                        content="Rate limit info", tier=1),
            ],
            total_searched=5,
            total_opened=2,
            total_used=1,
            confidence="high",
        )
        d = pkg.to_dict()
        serialized = json.dumps(d)
        deserialized = json.loads(serialized)
        assert len(deserialized["evidence"]) == 1
        assert deserialized["confidence"] == "high"

    def test_context_string_survives(self):
        """The context string should be usable by any LLM provider."""
        pkg = EvidencePackage(
            evidence=[
                Evidence(source_name="Groq", source_domain="console.groq.com",
                        url="https://groq.com/docs", title="Docs",
                        content="TPM limit is 8000", tier=1),
            ],
            confidence="high",
        )
        ctx = pkg.to_context_string()
        # Any LLM provider should be able to understand this format
        assert "Groq" in ctx
        assert "8000" in ctx
        assert "Tier 1" in ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Test 12: Favicon fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestFaviconFallback(unittest.TestCase):
    def test_favicon_url_always_returns_string(self):
        """favicon_url should always return a valid string."""
        assert isinstance(favicon_url("example.com"), str)
        assert isinstance(favicon_url(""), str)
        assert isinstance(favicon_url("a.b"), str)

    def test_favicon_uses_google_service(self):
        """favicon_url should use Google favicon service."""
        url = favicon_url("github.com")
        assert url.startswith("https://www.google.com/s2/favicons")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 13: Token budget enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenBudget(unittest.TestCase):
    def test_context_within_budget(self):
        """Context string should respect the token budget."""
        evidences = []
        for i in range(10):
            evidences.append(Evidence(
                source_name=f"Source {i}",
                source_domain=f"source{i}.com",
                url=f"https://source{i}.com/page",
                title=f"Page {i}",
                content="x" * 200,
                tier=3,
            ))
        pkg = EvidencePackage(evidence=evidences, confidence="medium")
        # Default budget is 3000 chars ≈ 750 tokens
        ctx = pkg.to_context_string()
        assert len(ctx) < EVIDENCE_BUDGET_CHARS + 500  # small overhead for headers

    def test_evidence_count_limit(self):
        """Should not exceed EVIDENCE_BUDGET_EVIDENCES items."""
        pkg = EvidencePackage(
            evidence=[Evidence(source_name=f"S{i}", url=f"https://s{i}.com") for i in range(20)],
        )
        meta = pkg.to_frontend_metadata()
        assert len(meta) <= 8  # frontend deduplicates by domain


# ═══════════════════════════════════════════════════════════════════════════════
# Test 14: SourceIntelligence class (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourceIntelligenceClass(unittest.TestCase):
    def test_build_evidence_package_empty(self):
        """Empty search results should produce empty package."""
        si = SourceIntelligence()
        pkg = si.build_evidence_package("", [], "Hello")
        assert pkg.confidence == "low"
        assert len(pkg.evidence) == 0

    def test_build_evidence_package_with_sources(self):
        """Should build evidence from existing sources."""
        si = SourceIntelligence()
        search_text = (
            "Live search results:\n"
            "- Groq Rate Limits: TPM is 8000 for free tier.\n"
            "- OpenAI Pricing: GPT-4 costs vary.\n"
        )
        sources = [
            {"title": "Groq Rate Limits", "url": "https://console.groq.com/docs", "domain": "console.groq.com"},
            {"title": "OpenAI Pricing", "url": "https://platform.openai.com/pricing", "domain": "platform.openai.com"},
        ]
        pkg = si.build_evidence_package(search_text, sources, "What are rate limits?")
        assert len(pkg.evidence) > 0
        assert pkg.confidence in ("low", "medium", "high")

    def test_build_evidence_package_survives_fallback(self):
        """Evidence package should be provider-agnostic."""
        si = SourceIntelligence()
        search_text = (
            "Live search results:\n"
            "- Groq Rate Limits: TPM is 8000.\n"
        )
        sources = [
            {"title": "Groq Rate Limits", "url": "https://console.groq.com/docs", "domain": "console.groq.com"},
        ]
        pkg = si.build_evidence_package(search_text, sources, "What are the limits?")
        # Serialize and deserialize — should survive provider fallback
        d = pkg.to_dict()
        restored = json.loads(json.dumps(d))
        assert len(restored["evidence"]) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Test 15: research_with_fetch uses structured results (URL regression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchWithFetchUsesStructuredResults(unittest.TestCase):
    """Regression: research_with_fetch must use structured results from
    _get_last_structured_results() which contain proper URLs, rather than
    _parse_search_text() which loses URLs from TinyFish output.

    Without this fix, evidence items had empty url/domain, which caused
    the frontend Sources panel to never render.
    """

    def test_structured_results_used_when_available(self):
        """When TinyFish provides structured results, they should be used."""
        from unittest.mock import patch

        structured = [
            {
                "title": "Bitcoin Price Today - CoinDesk",
                "url": "https://www.coindesk.com/price/bitcoin",
                "domain": "coindesk.com",
                "snippet": "Bitcoin traded at $111,745 on August 26.",
                "published": "",
            },
            {
                "title": "Bitcoin Price Chart - CoinMarketCap",
                "url": "https://coinmarketcap.com/currencies/bitcoin/",
                "domain": "coinmarketcap.com",
                "snippet": "Bitcoin price is updated in real-time.",
                "published": "",
            },
        ]

        fake_search_text = (
            "Live search results:\n"
            "- Bitcoin Price Today - CoinDesk: Bitcoin traded at $111,745.\n"
            "- Bitcoin Price Chart - CoinMarketCap: Bitcoin price is updated.\n"
        )

        def mock_general_web(query, site=""):
            return fake_search_text

        def mock_structured():
            return structured

        si = SourceIntelligence()
        with patch("core.external_apis._search_general_web", mock_general_web), \
             patch("core.external_apis.get_last_structured_results", mock_structured), \
             patch("core.external_apis._reset_search_sources"), \
             patch("core.external_apis._fetch_url_content", return_value="Fetched content about Bitcoin price at $111,745."):
            pkg = si.research_with_fetch("What is the current price of Bitcoin?")

        assert len(pkg.evidence) > 0, "Evidence should not be empty"
        for ev in pkg.evidence:
            assert ev.url, f"Evidence url should be populated, got: {ev.url!r}"
            assert ev.source_domain, f"Evidence domain should be populated, got: {ev.source_domain!r}"

    def test_frontend_metadata_has_urls(self):
        """Frontend metadata from evidence must include url and domain."""
        from unittest.mock import patch

        structured = [
            {
                "title": "Bitcoin Price Today - CoinDesk",
                "url": "https://www.coindesk.com/price/bitcoin",
                "domain": "coindesk.com",
                "snippet": "Bitcoin traded at $111,745.",
                "published": "",
            },
        ]

        si = SourceIntelligence()
        with patch("core.external_apis._search_general_web", return_value="Live search results:\n- Bitcoin Price Today: Bitcoin traded at $111,745.\n"), \
             patch("core.external_apis.get_last_structured_results", return_value=structured), \
             patch("core.external_apis._reset_search_sources"), \
             patch("core.external_apis._fetch_url_content", return_value="Fetched content about Bitcoin."):
            pkg = si.research_with_fetch("What is the current price of Bitcoin?")

        meta = pkg.to_frontend_metadata()
        assert len(meta) > 0, "Frontend metadata should not be empty"
        for m in meta:
            assert m["url"], f"Metadata url should be populated, got: {m['url']!r}"
            assert m["domain"], f"Metadata domain should be populated, got: {m['domain']!r}"

    def test_fallback_to_parse_when_no_structured(self):
        """When structured results are empty, should fall back to _parse_search_text.

        _parse_search_text extracts titles from formatted text but cannot recover
        URLs, so evidence items will have empty urls and get filtered. This is
        acceptable — brain.py's fallback path handles DDG/Wikipedia results.
        The key assertion is that the pipeline does not crash and returns a
        valid EvidencePackage.
        """
        from unittest.mock import patch

        fake_search_text = (
            "Live search results:\n"
            "- Groq Rate Limits: TPM limit is 8000.\n"
        )

        si = SourceIntelligence()
        with patch("core.external_apis._search_general_web", return_value=fake_search_text), \
             patch("core.external_apis.get_last_structured_results", return_value=[]), \
             patch("core.external_apis._reset_search_sources"), \
             patch("core.external_apis._fetch_url_content", return_value="Groq TPM limit is 8000 per minute."):
            pkg = si.research_with_fetch("What are Groq rate limits?")

        assert pkg is not None
        assert isinstance(pkg.evidence, list)
        assert pkg.total_searched >= 1

    def test_per_query_structured_isolation(self):
        """Each query should get its own structured results, not accumulated."""
        from unittest.mock import patch

        structured_q1 = [
            {"title": "Q1 Result", "url": "https://q1.com", "domain": "q1.com", "snippet": "Q1 info", "published": ""},
        ]
        structured_q2 = [
            {"title": "Q2 Result", "url": "https://q2.com", "domain": "q2.com", "snippet": "Q2 info", "published": ""},
        ]

        query_calls = {"n": 0}

        def mock_general_web(query, site=""):
            query_calls["n"] += 1
            return "Live search results:\n- Result\n"

        def mock_structured():
            return structured_q1 if query_calls["n"] == 1 else structured_q2

        si = SourceIntelligence()
        with patch("core.external_apis._search_general_web", mock_general_web), \
             patch("core.external_apis.get_last_structured_results", mock_structured), \
             patch("core.external_apis._reset_search_sources"), \
             patch("core.external_apis._fetch_url_content", return_value="Content"):
            pkg = si.research_with_fetch("What is Bitcoin price and Ethereum price?")

        domains = {ev.source_domain for ev in pkg.evidence}
        assert "q1.com" in domains or "q2.com" in domains


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: Freshness keyword detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestFreshnessKeywords(unittest.TestCase):
    def test_price_keyword_triggers_freshness(self):
        """'price of Bitcoin' should trigger needs_freshness."""
        plan = plan_search("What is the price of Bitcoin right now?")
        assert plan.needs_freshness is True

    def test_right_now_keyword(self):
        """'right now' should trigger needs_freshness."""
        plan = plan_search("What is the current price right now?")
        assert plan.needs_freshness is True

    def test_generic_question_no_freshness(self):
        """'How does Bitcoin work?' should NOT trigger freshness."""
        plan = plan_search("How does Bitcoin work?")
        assert plan.needs_freshness is False

    def test_freshness_query_appended(self):
        """When needs_freshness=True, a supplementary freshness query should be added."""
        plan = plan_search("Bitcoin price right now")
        assert plan.needs_freshness is True
        # Should have at least 2 queries: original + freshness variant
        assert len(plan.queries) >= 2
        # At least one query should contain 'today' or the current year
        year = str(__import__("datetime").datetime.now().year)
        has_freshness_query = any(
            "today" in q.lower() or year in q
            for q in plan.queries
        )
        assert has_freshness_query, f"No freshness query found in {plan.queries}"


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: Smart intent detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestSmartIntentDetection(unittest.TestCase):
    def test_reply_write_detected(self):
        """Reply-writing requests should be classified as 'none' intent."""
        from unittest.mock import patch, MagicMock
        from core.external_apis import classify_research_intent
        with patch("core.external_apis.get_config") as mock_cfg, \
             patch("core.external_apis.get_latest_groq_model", return_value="test-model"):
            mock_cfg.return_value = MagicMock(groq_api_key="fake", groq_base_url="")
            result = classify_research_intent("Write a reply to this email")
            assert result["intent"] == "none"
            assert result["reason"] == "reply-writing"

    def test_copy_box_detected(self):
        """Copy-box requests should be classified as 'none' intent."""
        from unittest.mock import patch, MagicMock
        from core.external_apis import classify_research_intent
        with patch("core.external_apis.get_config") as mock_cfg, \
             patch("core.external_apis.get_latest_groq_model", return_value="test-model"):
            mock_cfg.return_value = MagicMock(groq_api_key="fake", groq_base_url="")
            result = classify_research_intent("copy that")
            assert result["intent"] == "none"
            assert result["reason"] == "copy-box"

    def test_copy_this_detected(self):
        """'copy this' should be classified as 'none' intent."""
        from unittest.mock import patch, MagicMock
        from core.external_apis import classify_research_intent
        with patch("core.external_apis.get_config") as mock_cfg, \
             patch("core.external_apis.get_latest_groq_model", return_value="test-model"):
            mock_cfg.return_value = MagicMock(groq_api_key="fake", groq_base_url="")
            result = classify_research_intent("copy this")
            assert result["intent"] == "none"
            assert result["reason"] == "copy-box"


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: Freshness category assignment
# ═══════════════════════════════════════════════════════════════════════════════

class TestFreshnessCategory(unittest.TestCase):
    def test_news_query_gets_current_category(self):
        """News queries should get freshness_category='current'."""
        from unittest.mock import patch
        si = SourceIntelligence()
        with patch("core.external_apis._search_general_web", return_value=""), \
             patch("core.external_apis.get_last_structured_results", return_value=[]), \
             patch("core.external_apis._reset_search_sources"):
            pkg = si.research_with_fetch("What happened in the news today?")
        assert pkg.freshness_category == "current"

    def test_tech_docs_get_background_category(self):
        """Technical documentation queries should get freshness_category='background'."""
        from unittest.mock import patch
        si = SourceIntelligence()
        with patch("core.external_apis._search_general_web", return_value=""), \
             patch("core.external_apis.get_last_structured_results", return_value=[]), \
             patch("core.external_apis._reset_search_sources"):
            pkg = si.research_with_fetch("How does Docker networking work?")
        assert pkg.freshness_category == "background"

    def test_freshness_in_context_string(self):
        """Freshness category should appear in the context string."""
        pkg = EvidencePackage(
            evidence=[
                Evidence(
                    source_name="Coinbase", source_domain="coinbase.com",
                    source_icon="", source_type="web", url="https://coinbase.com",
                    title="Bitcoin Price", content="BTC is $67000", tier=2,
                )
            ],
            total_searched=5, total_used=1, confidence="high",
            freshness_category="current",
        )
        ctx = pkg.to_context_string()
        assert "CURRENT" in ctx

    def test_freshness_in_frontend_metadata(self):
        """Freshness category should appear in frontend metadata."""
        pkg = EvidencePackage(
            evidence=[
                Evidence(
                    source_name="Coinbase", source_domain="coinbase.com",
                    source_icon="", source_type="web", url="https://coinbase.com",
                    title="Bitcoin Price", content="BTC is $67000", tier=2,
                )
            ],
            total_searched=5, total_used=1, confidence="high",
            freshness_category="current",
        )
        meta = pkg.to_frontend_metadata()
        assert len(meta) == 1
        assert meta[0]["freshness_category"] == "current"

    def test_freshness_in_to_dict(self):
        """Freshness category should appear in to_dict()."""
        pkg = EvidencePackage(
            evidence=[], confidence="low",
            freshness_category="background",
        )
        d = pkg.to_dict()
        assert d["freshness_category"] == "background"


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: SSE event yields are dicts (not json.dumps strings)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSSEDictYields(unittest.TestCase):
    def test_stream_respond_yields_dicts_for_structured_events(self):
        """All structured SSE events (thinking_process, intent, recovery_status)
        should be yielded as plain dicts, not json.dumps strings.

        This is critical because app.py's dispatcher uses isinstance(token, dict)
        to distinguish structured events from text tokens. If brain.py yields
        json.dumps strings, they get silently re-wrapped as {"token": "..."},
        and the frontend never sees the original event structure.
        """
        import ast
        import re

        # Read brain.py source
        with open("core/brain.py", "r") as f:
            source = f.read()

        # Find stream_respond method body (full method, not a fixed window)
        start = source.find("def stream_respond(self, message: str")
        assert start > 0, "stream_respond method not found"
        # Find the end: next "    def " at the same 4-space indent, or end of file
        method_end = len(source)
        for m in re.finditer(r"^    def ", source, re.MULTILINE):
            if m.start() > start:
                method_end = m.start()
                break
        method_body = source[start:method_end]

        # Check that structured events yield dicts, not json.dumps
        # Pattern: yield {"thinking_process": ...} or yield {"intent": ...} or yield {"recovery_status": ...}
        dict_yields = re.findall(
            r'yield\s+\{(?!.*json\.dumps).*"(?:thinking_process|intent|recovery_status|token)"',
            method_body,
        )
        # Should have at least 5 dict yields for the structured events
        assert len(dict_yields) >= 5, (
            f"Expected >= 5 structured dict yields in stream_respond, found {len(dict_yields)}. "
            f"Structured events must be yielded as dicts, not json.dumps strings."
        )

    def test_no_json_dumps_for_structured_events(self):
        """Ensure no json.dumps() wrapping for structured SSE events."""
        with open("core/brain.py", "r") as f:
            source = f.read()

        start = source.find("def stream_respond(self, message: str")
        method_end = len(source)
        for m in re.finditer(r"^    def ", source, re.MULTILINE):
            if m.start() > start:
                method_end = m.start()
                break
        method_body = source[start:method_end]

        bad_yields = re.findall(
            r'yield\s+json\.dumps\(\{[^}]*(?:thinking_process|recovery_status)',
            method_body,
        )
        assert len(bad_yields) == 0, (
            f"Found {len(bad_yields)} json.dumps() yields for structured events: {bad_yields}. "
            f"These should be plain dict yields."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: System prompt contains expected sections
# ═══════════════════════════════════════════════════════════════════════════════

class TestSystemPromptSections(unittest.TestCase):
    def test_citations_use_source_names(self):
        """System prompt should instruct LLM to use [SourceName] citations."""
        from core.brain import _CHAT_SYSTEM_PROMPT
        assert "SourceName" in _CHAT_SYSTEM_PROMPT or "[Coinbase]" in _CHAT_SYSTEM_PROMPT or "[Wikipedia]" in _CHAT_SYSTEM_PROMPT

    def test_freshness_awareness_section(self):
        """System prompt should contain freshness awareness instructions."""
        from core.brain import _CHAT_SYSTEM_PROMPT
        assert "FRESHNESS AWARENESS" in _CHAT_SYSTEM_PROMPT

    def test_smart_behavior_section(self):
        """System prompt should contain smart behavior instructions."""
        from core.brain import _CHAT_SYSTEM_PROMPT
        assert "SMART BEHAVIOR" in _CHAT_SYSTEM_PROMPT

    def test_personality_section(self):
        """System prompt should contain conversational style instructions."""
        from core.brain import _CHAT_SYSTEM_PROMPT
        assert "CONVERSATIONAL STYLE" in _CHAT_SYSTEM_PROMPT

    def test_conflicting_numbers_section(self):
        """System prompt should contain conflicting numbers instructions."""
        from core.brain import _CHAT_SYSTEM_PROMPT
        assert "CONFLICTING NUMBERS" in _CHAT_SYSTEM_PROMPT

    def test_financial_assets_section(self):
        """System prompt should contain financial assets instructions."""
        from core.brain import _CHAT_SYSTEM_PROMPT
        assert "FINANCIAL ASSETS" in _CHAT_SYSTEM_PROMPT

    def test_answer_length_section(self):
        """System prompt should contain answer length instructions."""
        from core.brain import _CHAT_SYSTEM_PROMPT
        assert "ANSWER LENGTH" in _CHAT_SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: Published-date parsing and freshness labels
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublishedDateParsing(unittest.TestCase):
    def test_iso_date(self):
        dt = _parse_published_date("2026-08-26T12:34:56Z")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 8 and dt.day == 26

    def test_iso_date_only(self):
        dt = _parse_published_date("2026-08-26")
        assert dt is not None and dt.day == 26

    def test_slash_date(self):
        dt = _parse_published_date("2026/08/26")
        assert dt is not None and dt.day == 26

    def test_short_month_date(self):
        dt = _parse_published_date("Aug 26, 2026")
        assert dt is not None and dt.month == 8 and dt.day == 26

    def test_long_month_date(self):
        dt = _parse_published_date("August 26, 2026")
        assert dt is not None and dt.month == 8

    def test_day_month_year(self):
        dt = _parse_published_date("26 Aug 2026")
        assert dt is not None and dt.month == 8 and dt.day == 26

    def test_unparseable_returns_none(self):
        assert _parse_published_date("") is None
        assert _parse_published_date("nonsense") is None
        assert _parse_published_date(None) is None

    def test_invalid_date_returns_none(self):
        assert _parse_published_date("2026-13-45") is None


class TestAgeLabel(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        self.now = datetime(2026, 8, 27, 12, 0, 0)

    def test_today(self):
        age, label = _age_label("2026-08-27", self.now)
        assert label == "today"

    def test_yesterday(self):
        age, label = _age_label("2026-08-26", self.now)
        assert label == "yesterday"

    def test_recent(self):
        age, label = _age_label("2026-08-22", self.now)
        assert label == "recent"

    def test_old(self):
        age, label = _age_label("2026-07-01", self.now)
        assert label == "old"

    def test_unparseable(self):
        age, label = _age_label("", self.now)
        assert age is None and label == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: Freshness-aware ranking + financial market-data boost
# ═══════════════════════════════════════════════════════════════════════════════

class TestFreshnessRanking(unittest.TestCase):
    def test_today_ranks_above_old_for_freshness(self):
        """For a freshness query, a same-day source should outrank an old one."""
        plan = SearchPlan(
            queries=["bitcoin price"],
            needs_freshness=True,
            domain="finance",
        )
        results = [
            {"url": "https://news.com/bitcoin", "title": "Bitcoin", "domain": "news.com",
             "snippet": "Bitcoin market news", "published": "2026-08-20"},
            {"url": "https://coinmarketcap.com/bitcoin", "title": "Bitcoin price", "domain": "coinmarketcap.com",
             "snippet": "Bitcoin today", "published": "2026-08-27"},
        ]
        ranked = _rank_results(results, plan)
        assert ranked[0]["domain"] == "coinmarketcap.com", (
            "Same-day market-data source should rank above an older news source"
        )

    def test_finance_predictions_penalized(self):
        """Prediction/forecast sources should be demoted for finance queries."""
        plan = SearchPlan(
            queries=["bitcoin price"],
            needs_freshness=True,
            domain="finance",
        )
        results = [
            {"url": "https://predictor.io/btc", "title": "Bitcoin price prediction", "domain": "predictor.io",
             "snippet": "Our prediction for bitcoin forecast", "published": "2026-08-27"},
            {"url": "https://coingecko.com/bitcoin", "title": "Bitcoin", "domain": "coingecko.com",
             "snippet": "Live bitcoin price", "published": "2026-08-27"},
        ]
        ranked = _rank_results(results, plan)
        # Coingecko (market data) should beat a prediction site
        assert ranked[0]["domain"] == "coingecko.com"

    def test_market_data_source_type(self):
        """Exchange/market-data domains should classify with market source_type."""
        src = classify_domain("coinbase.com")
        assert src.source_type == "exchange_market_data"
        src2 = classify_domain("coinmarketcap.com")
        assert src2.source_type == "market_data"
        src3 = classify_domain("bloomberg.com")
        assert src3.source_type == "financial_news"

    def test_finance_domain_detection(self):
        """A bitcoin price query should be detected as finance domain."""
        plan = plan_search("What is the current price of Bitcoin right now?")
        assert plan.domain == "finance", f"expected finance, got {plan.domain}"

    def test_freshness_labels_attached(self):
        """_rank_results should attach _age_label to each result."""
        plan = SearchPlan(queries=["x"], needs_freshness=True)
        results = [
            {"url": "https://a.com", "title": "A", "domain": "a.com",
             "snippet": "content", "published": "2026-08-26"},
        ]
        ranked = _rank_results(results, plan)
        assert ranked[0]["_age_label"] in ("today", "yesterday", "recent", "old")
        assert "_age_days" in ranked[0]


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: Evidence freshness_label + context string freshness instructions
# ═══════════════════════════════════════════════════════════════════════════════

class TestFreshnessContext(unittest.TestCase):
    def test_evidence_carries_freshness_label(self):
        result = {
            "title": "Bitcoin price",
            "url": "https://coinmarketcap.com/bitcoin",
            "domain": "coinmarketcap.com",
            "snippet": "Bitcoin today price",
            "published": "2026-08-26",
            "_score": 0.8,
            "_source": classify_domain("coinmarketcap.com"),
        }
        ev = _build_evidence_from_snippet(result)
        assert ev.freshness_label in ("today", "yesterday", "recent", "old", "")

    def test_context_string_includes_today_date(self):
        from core.source_intelligence import EvidencePackage, EVIDENCE_BUDGET_CHARS
        ev = Evidence(
            source_name="Coinbase", source_domain="coinbase.com", source_icon="",
            source_type="exchange_market_data", url="https://coinbase.com",
            title="BTC", content="Bitcoin price", supports_claims=[],
            published="2026-08-27", accessed_at="", tier=1,
            freshness_label="today",
        )
        pkg = EvidencePackage(evidence=[ev])
        ctx = pkg.to_context_string()
        assert "TODAY'S DATE" in ctx
        assert "NEVER relabel" in ctx
        assert "Freshness: today" in ctx

    def test_data_type_current_header(self):
        from core.source_intelligence import EvidencePackage
        ev = Evidence(
            source_name="Coinbase", source_domain="coinbase.com", source_icon="",
            source_type="exchange_market_data", url="https://coinbase.com",
            title="BTC", content="price", supports_claims=[],
            published="2026-08-27", accessed_at="", tier=1, freshness_label="today",
        )
        pkg = EvidencePackage(evidence=[ev], freshness_category="current")
        ctx = pkg.to_context_string()
        assert "DATA TYPE: CURRENT" in ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: MODE B reply mode SSE flag
# ═══════════════════════════════════════════════════════════════════════════════

class TestReplyMode(unittest.TestCase):
    def test_stream_respond_yields_reply_mode(self):
        """stream_respond should yield a reply_mode dict in MODE B (reply/copy) intent."""
        import re
        with open("core/brain.py", "r") as f:
            source = f.read()
        start = source.find("def stream_respond(self, message: str")
        assert start > 0, "stream_respond method not found"
        method_body = source[start:start + 9000]
        # Reply-mode flag yield present
        assert 'reply_mode' in method_body, "stream_respond should reference reply_mode"

    def test_done_events_include_reply_mode(self):
        """app.py dispatch done events should include reply_mode from the brain."""
        with open("app.py", "r") as f:
            source = f.read()
        assert "reply_mode" in source, "app.py should include reply_mode in done events"


if __name__ == "__main__":
    unittest.main()
