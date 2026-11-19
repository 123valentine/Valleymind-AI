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
