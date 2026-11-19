"""Regression coverage for TinyFish fetch metadata -> entity logo propagation."""


def test_tinyfish_fetch_uses_urls_array_and_preserves_logo(monkeypatch):
    from core import external_apis as ea

    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setattr(ea, "provider_blocked", lambda name: False)
    monkeypatch.setattr(ea, "record_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(ea, "record_error", lambda *args, **kwargs: None)

    called = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "errors": [],
                "results": [{
                    "url": "https://example.com/",
                    "final_url": "https://example.com/",
                    "title": "Example",
                    "text": "Real page content",
                    "logo": "https://cdn.example.com/logo?id=7",
                }],
            }

    def fake_post(url, json, headers, timeout):
        called["url"] = url
        called["json"] = json
        called["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(ea.requests, "post", fake_post)

    content, metadata = ea._fetch_url_with_metadata("https://example.com/")

    assert called["url"] == "https://api.fetch.tinyfish.ai"
    assert called["json"] == {"urls": ["https://example.com/"]}
    assert called["headers"]["X-API-Key"] == "test-key"
    assert content == "Real page content"
    assert metadata["logo"] == "https://cdn.example.com/logo?id=7"
    assert metadata["title"] == "Example"
    assert ea._fetch_url_content("https://example.com/") == "Real page content"


def test_evidence_metadata_exposes_logo():
    from core.source_intelligence import Evidence, EvidencePackage, favicon_url

    icon = favicon_url("example.com")
    assert "sz=128" in icon and "domain=example.com" in icon

    package = EvidencePackage(evidence=[Evidence(
        source_name="Example",
        source_domain="example.com",
        source_icon=icon,
        logo="https://cdn.example.com/logo.png",
        title="Example",
        content="Evidence",
        url="https://example.com/",
    )])
    source = package.to_frontend_metadata()[0]
    assert source["logo"] == "https://cdn.example.com/logo.png"
    assert source["icon"] == icon
    assert "Logo/Icon URL: https://cdn.example.com/logo.png" in package.to_context_string()


def test_query_image_filter_keeps_logo_metadata():
    from core.brain import _filter_query_images

    filtered = _filter_query_images([{
        "domain": "example.com",
        "logo": "https://cdn.example.com/logo.png",
        "icon": "https://www.google.com/s2/favicons?sz=128&domain=example.com",
        "image": "https://example.com/hero-banner.jpg",
    }], "pricing and context window for example.com")

    assert filtered[0]["logo"] == "https://cdn.example.com/logo.png"
    assert "sz=128" in filtered[0]["icon"]
    assert filtered[0]["image"] == ""


def test_query_allows_images_gate(monkeypatch):
    from core import external_apis as ea

    assert ea._query_allows_images("show me photos of the models") is True
    assert ea._query_allows_images("query") is True
    assert ea._query_allows_images("list the top AI models with logos") is False
    assert ea._query_allows_images("pricing and context window comparison") is False


def test_list_query_skips_blocking_page_head_scrape(monkeypatch):
    from core import external_apis as ea

    calls = []

    def fake_scrape(url):
        calls.append(url)
        return ({"image_url": "https://cdn.test/hero.jpg", "image_credit": "cdn"},
                {"logo_url": "https://cdn.test/logo.png", "image_credit": "cdn"})

    monkeypatch.setattr(ea, "_source_visual_metadata_from_page", fake_scrape)
    results = [{"url": "https://example.com/story", "title": "Example"}]

    ea._enrich_results_with_images(results, "list the top AI models with logos")

    assert calls == []
    assert "image" not in results[0]

    # Visual intent must still use the guarded page-head lookup.
    ea._enrich_results_with_images(results, "show me photos of AI models")
    assert calls == ["https://example.com/story"]


def test_fetch_metadata_accepts_tight_timeout(monkeypatch):
    from core import external_apis as ea

    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setattr(ea, "provider_blocked", lambda name: False)
    monkeypatch.setattr(ea, "record_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(ea, "record_error", lambda *args, **kwargs: None)

    seen = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"results": [{"url": "https://example.com/", "text": "ok"}]}

    def fake_post(url, json, headers, timeout):
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ea.requests, "post", fake_post)
    ea._fetch_url_with_metadata("https://example.com/", timeout=7)
    assert seen["timeout"] == 7


def _research_results(count=5, snippet="short"):
    return [
        {
            "title": f"Result {i}",
            "url": f"https://example{i}.com/story",
            "domain": f"example{i}.com",
            "snippet": snippet,
            "published": "",
        }
        for i in range(count)
    ]


def test_simple_list_query_skips_full_page_fetches(monkeypatch):
    from unittest.mock import patch
    from core import source_intelligence as si

    calls = []

    def fake_fetch(url, max_chars=4000, timeout=8):
        calls.append(url)
        return "fetched page", {}

    rich_snippet = "A detailed summary of the subject with enough metadata for entity cards. " * 2
    with patch("core.external_apis._search_general_web", return_value="Live search results:"), \
         patch("core.external_apis.get_last_structured_results",
               return_value=_research_results(3, rich_snippet)), \
         patch("core.external_apis._reset_search_sources"), \
         patch("core.external_apis._fetch_url_with_metadata", fake_fetch), \
         patch("core.external_apis._enrich_results_with_images"):
        pkg = si.SourceIntelligence().research_with_fetch("list the top AI models with logos")

    assert calls == []
    assert pkg.total_opened == 0


def test_page_fetches_use_four_workers(monkeypatch):
    from unittest.mock import patch
    from core import source_intelligence as si

    real_pool = si.ThreadPoolExecutor
    workers = []

    def tracking_pool(max_workers):
        workers.append(max_workers)
        return real_pool(max_workers)

    def fake_fetch(url, max_chars=4000, timeout=8):
        return "fetched page", {}

    monkeypatch.setattr(si, "ThreadPoolExecutor", tracking_pool)
    with patch("core.external_apis._search_general_web", return_value="Live search results:"), \
         patch("core.external_apis.get_last_structured_results",
               return_value=_research_results(5)), \
         patch("core.external_apis._reset_search_sources"), \
         patch("core.external_apis._fetch_url_with_metadata", fake_fetch), \
         patch("core.external_apis._enrich_results_with_images"):
        pkg = si.SourceIntelligence().research_with_fetch("recent technology news")

    assert workers == [4]
    assert pkg.total_opened == 5


def test_search_general_web_prefers_google_first(monkeypatch):
    from core import external_apis as ea

    calls = []

    def fake_google(query, site=""):
        calls.append(("google", query, site))
        return "Live search results:\n- Google: result"

    def fake_tiny(query, site=""):
        calls.append(("tinyfish", query, site))
        return "Live search results:\n- Tiny: result"

    def fake_ddg(query):
        calls.append(("ddg", query))
        return "Live search results:\n- DDG: result"

    monkeypatch.setattr(ea, "_search_google", fake_google)
    monkeypatch.setattr(ea, "_search_tinyfish", fake_tiny)
    monkeypatch.setattr(ea, "_search_ddg_lite", fake_ddg)
    monkeypatch.setattr(ea, "_search_wikipedia", lambda q: "Live search results:\n- Wiki: result")

    out = ea._search_general_web("real madrid latest news")

    assert out.startswith("Live search results:")
    assert calls == [("google", "real madrid latest news", "")]


def test_search_general_web_falls_back_when_google_unavailable(monkeypatch):
    from core import external_apis as ea

    calls = []

    monkeypatch.setattr(ea, "_search_google", lambda q, site="": calls.append("google") or "")
    monkeypatch.setattr(ea, "_search_tinyfish", lambda q, site="": calls.append("tinyfish") or "Live search results:\n- Tiny: ok")
    monkeypatch.setattr(ea, "_search_ddg_lite", lambda q: calls.append("ddg") or "")
    monkeypatch.setattr(ea, "_search_wikipedia", lambda q: calls.append("wiki") or "")

    out = ea._search_general_web("anthropic claude")

    assert "Tiny: ok" in out
    assert calls == ["google", "tinyfish"]
