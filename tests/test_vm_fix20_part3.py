import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.external_apis import (
    clean_search_query,
    _is_private_or_local_host,
    _looks_like_private_individual,
    _resolve_url,
    _extract_og_image_from_html,
    _host_credit,
    _enrich_results_with_images,
)

ok = 0
def t(name, cond):
    global ok
    if cond: ok += 1
    else: print("FAIL:", name)

# --- query cleaning (Part 2, re-verified) ---
t("strip list filler", clean_search_query("List 3 AI tools with their logos").lower().count("list") == 0)
t("keep iPhone model", "iphone" in clean_search_query("What is the latest iPhone model").lower())
t("keep temporal", "latest" in clean_search_query("What is the latest iPhone model").lower())

# --- SSRF / private address blocking ---
t("block localhost", _is_private_or_local_host("localhost") is True)
t("block 127.0.0.1", _is_private_or_local_host("127.0.0.1") is True)
t("block 169.254", _is_private_or_local_host("169.254.169.254") is True)
t("block RFC1918 10.x", _is_private_or_local_host("10.0.0.5") is True)
t("allow public domain", _is_private_or_local_host("example.com") is False)
t("allow wikipedia", _is_private_or_local_host("en.wikipedia.org") is False)

# --- private-individual guard ---
t("block email signal", _looks_like_private_individual("contact jane@x.com") is True)
t("block 'my ' signal", _looks_like_private_individual("my private doctor") is True)
t("allow public figure", _looks_like_private_individual("Albert Einstein biography") is False)

# --- URL resolution ---
t("resolve relative", _resolve_url("/img.png", "https://example.com/page") == "https://example.com/img.png")

# --- og:image extraction from HTML ---
html = '''<html><head><title>Example Page</title>
<meta property="og:image" content="https://example.com/img.jpg">
<meta name="twitter:image" content="https://example.com/tw.jpg">
</head></html>'''
r = _extract_og_image_from_html(html, "https://example.com/page")
t("og:image https returned", r is not None and r["image_url"] == "https://example.com/img.jpg")
t("og:image credit from title", r and r["image_credit"] == "Example Page")

# twitter:image fallback when no og:image
html2 = '<title>Tw Page</title><meta name="twitter:image" content="https://example.com/tw.jpg">'
r2 = _extract_og_image_from_html(html2, "https://example.com/page")
t("twitter:image fallback", r2 is not None and r2["image_url"] == "https://example.com/tw.jpg")

# http image rejected
html3 = '<title>X</title><meta property="og:image" content="http://example.com/img.jpg">'
r3 = _extract_og_image_from_html(html3, "https://example.com/page")
t("http image rejected", r3 is None)

# empty html
t("empty html none", _extract_og_image_from_html("", "https://example.com/") is None)

# --- host credit ---
t("host credit", _host_credit("https://www.example.com/path") == "Www")

# --- enrichment: attaches image to https results, skips http (monkeypatch network) ---
import core.external_apis as ea
ea._source_image_from_page = lambda url: {"image_url": "https://cdn.example.com/img.jpg", "image_credit": "Example"} if url.startswith("https://") else None
ea._wikimedia_image = lambda q: None
res = [
    {"title": "A", "url": "https://example.com/a", "domain": "example.com"},
    {"title": "B", "url": "http://example.com/b", "domain": "example.com"},  # http -> skipped
]
_enrich_results_with_images(res, "query")
t("https result enriched", res[0].get("image") == "https://cdn.example.com/img.jpg")
t("http result not enriched", res[1].get("image") in (None, ""))

print("VM_FIX20_PART3: %d checks passed" % ok)
if ok < 1:
    sys.exit(1)
