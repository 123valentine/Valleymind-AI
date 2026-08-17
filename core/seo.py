"""Search-engine optimisation (SEO) support for ValleyMind AI.

This module is the single source of truth for everything Google needs to
understand and index the site:

  * ``SITE_URL``              – the public base URL (canonical host).
  * ``SITE_NAME``/``SITE_DESCRIPTION`` – shared brand metadata.
  * ``PUBLIC_PAGES``          – the registry of every public page. Adding one
                                dict here + one template in ``/templates`` is
                                ALL that is needed to expose a new page to
                                search engines: the Flask route, the
                                ``sitemap.xml`` entry and the meta/JSON-LD
                                output all follow automatically.
  * ``robots_txt()``          – production ``robots.txt`` body.
  * ``sitemap_xml()``         – auto-generated ``sitemap.xml`` body (includes
                                last-modified dates derived from file mtimes).
  * ``render_page(key)``      – renders a server-side marketing page with the
                                full SEO head (meta, canonical, Open Graph,
                                Twitter card, Schema.org JSON-LD).

The main SPA app (``index.html``, served at ``/``) keeps its own static
``<head>`` in the file; these marketing pages are server-rendered so their
content is indexable without a headless browser.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from flask import render_template

from core.config import PROJECT_ROOT

# ── Public site identity ────────────────────────────────────────────────────
# Resolve the canonical host. Priority:
#   1. SITE_URL            – set this once your custom domain (valleymind.ai)
#                            is wired up in DNS. Canonical tags, the sitemap,
#                            Open Graph URLs and JSON-LD all use this value.
#   2. RENDER_EXTERNAL_URL – auto-injected by Render with the live public URL.
#   3. Hard default        – the current production app URL.
SITE_URL = (
    os.getenv("SITE_URL", "").strip().rstrip("/")
    or os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    or "https://valleymind-ai-opms.onrender.com"
)

SITE_NAME = "ValleyMind AI"
SITE_TAGLINE = "AI Assistant, Image & Video Studio"
SITE_DESCRIPTION = (
    "ValleyMind AI is an intelligent AI assistant with long-term memory, "
    "image and video generation, a full creative studio and multi-persona "
    "round-table conversations. Try it free."
)

# Shared asset URLs (also used by Open Graph / Twitter / Schema.org).
LOGO_URL = f"{SITE_URL}/static/valleymind-logo.png"
OG_IMAGE_URL = f"{SITE_URL}/static/og-image.png"
FAVICON_URL = f"{SITE_URL}/static/favicon.ico"
APPLE_TOUCH_ICON_URL = f"{SITE_URL}/static/icons/apple-touch-icon.png"

# External profiles for the Organization `sameAs` field (brand verification).
SAME_AS = [
    "https://github.com/123valentine/Valleymind-AI",
    "https://huggingface.co/spaces/ValentineEgbujie/valleymindai",
]

# ── Public page registry ────────────────────────────────────────────────────
# Each entry maps a public URL to its template + SEO metadata. `template=None`
# means the URL is served by the SPA (index.html) rather than a Jinja template.
# `priority`/`changefreq` follow sitemap.org conventions (lowercase).
PUBLIC_PAGES = [
    {
        "key": "home",
        "path": "/",
        "template": None,  # served by the SPA
        "title": f"{SITE_NAME} — {SITE_TAGLINE}",
        "description": SITE_DESCRIPTION,
        "priority": "1.0",
        "changefreq": "daily",
    },
    {
        "key": "about",
        "path": "/about",
        "template": "about.html",
        "title": "About ValleyMind AI — Our Story & Mission",
        "description": (
            "Learn about ValleyMind AI: the intelligent assistant with "
            "memory, and the creative studio that turns prompts into images, "
            "videos and full productions."
        ),
        "priority": "0.7",
        "changefreq": "monthly",
    },
    {
        "key": "features",
        "path": "/features",
        "template": "features.html",
        "title": "ValleyMind AI Features — Chat, Generate, Create & Edit",
        "description": (
            "Explore ValleyMind AI features: AI chat with long-term memory, "
            "the multi-persona Round Table, image & video generation, the "
            "Studio editor, auto video editing and a built-in media library."
        ),
        "priority": "0.8",
        "changefreq": "weekly",
    },
    {
        "key": "pricing",
        "path": "/pricing",
        "template": "pricing.html",
        "title": "ValleyMind AI Pricing — Free to Start",
        "description": (
            "ValleyMind AI is free to start. Compare the Free plan with the "
            "coming Pro and Creator plans for more generations, the Studio "
            "and priority support."
        ),
        "priority": "0.8",
        "changefreq": "monthly",
    },
    {
        "key": "help",
        "path": "/help",
        "template": "help.html",
        "title": "Help & Support — ValleyMind AI",
        "description": (
            "Get help with ValleyMind AI: getting started, what the "
            "assistant can do, accounts & security, video generation and "
            "storage — plus how to reach support."
        ),
        "priority": "0.6",
        "changefreq": "monthly",
    },
    {
        "key": "contact",
        "path": "/contact",
        "template": "contact.html",
        "title": "Contact ValleyMind AI",
        "description": (
            "Contact the ValleyMind AI team: send feedback, report a "
            "problem or reach support by email, WhatsApp or through the "
            "in-app suggestion tool."
        ),
        "priority": "0.5",
        "changefreq": "yearly",
    },
    {
        "key": "privacy",
        "path": "/privacy",
        "template": "privacy.html",
        "title": "Privacy Policy — ValleyMind AI",
        "description": (
            "How ValleyMind AI collects, uses and protects your personal "
            "data, the third-party services it relies on, your rights and "
            "how to contact us about privacy."
        ),
        "priority": "0.3",
        "changefreq": "yearly",
    },
    {
        "key": "terms",
        "path": "/terms",
        "template": "terms.html",
        "title": "Terms of Service — ValleyMind AI",
        "description": (
            "The Terms of Service that govern your use of ValleyMind AI, "
            "including acceptable use, AI-generated content, our disclaimers "
            "and your responsibilities."
        ),
        "priority": "0.3",
        "changefreq": "yearly",
    },
]

# Canonical aliases → 301 redirect to the canonical page (avoids duplicate
# content in the index and keeps legacy URLs working).
URL_ALIASES = {
    "/privacy-policy": "/privacy",
    "/terms-of-service": "/terms",
}

# Nav / footer link order shown on the marketing pages.
NAV_LINKS = [
    {"path": "/features", "label": "Features"},
    {"path": "/pricing", "label": "Pricing"},
    {"path": "/about", "label": "About"},
    {"path": "/help", "label": "Help"},
    {"path": "/contact", "label": "Contact"},
]


def _page(key: str) -> dict:
    for entry in PUBLIC_PAGES:
        if entry["key"] == key:
            return entry
    raise KeyError(f"Unknown public page key: {key!r}")


def _last_modified(page: dict) -> datetime:
    """Last-modified timestamp for a page.

    For Jinja pages we use the template's mtime (so editing copy refreshes the
    sitemap automatically). For the SPA home we use index.html's mtime.
    """
    if page.get("template"):
        path = PROJECT_ROOT / "templates" / page["template"]
    else:
        path = PROJECT_ROOT / "index.html"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)


def canonical_url(path: str) -> str:
    """Absolute canonical URL for a site-relative path."""
    return f"{SITE_URL}{path}"


# ── robots.txt ──────────────────────────────────────────────────────────────
def robots_txt() -> str:
    """Production robots.txt.

    * ``Allow: /``            – every search engine may crawl the public site.
    * ``Disallow``            – private/system-only areas and API endpoints
                                (nothing there is worth indexing, and keeping
                                them out protects against accidental exposure
                                of user data in search results).
    * ``Sitemap:``            – points crawlers at the generated sitemap.
    """
    disallows = [
        "/api/",          # JSON/SSE API endpoints
        "/auth/",         # authentication endpoints
        "/chat",          # chat history / sessions
        "/suggestions",   # feedback intake
        "/tts/",          # generated speech audio
        "/share/",        # SPA share pages (duplicate of home content)
        "/phone-studio",  # app-shell alias (duplicate of home)
        "/studio",        # studio job endpoints
        "/static/media/users/",  # per-user generated media (private)
        "/memory_data/",  # local fallback store (never public)
    ]
    lines = [
        "# robots.txt for ValleyMind AI",
        "# Generated by core/seo.py — edit that module, not this file.",
        "",
        "User-agent: *",
        "Allow: /",
        *[f"Disallow: {d}" for d in disallows],
        "",
        f"Sitemap: {SITE_URL}/sitemap.xml",
        "",
    ]
    return "\n".join(lines)


# ── sitemap.xml ─────────────────────────────────────────────────────────────
def sitemap_xml() -> str:
    """Auto-generated XML sitemap covering every public page.

    Because it is generated from PUBLIC_PAGES at request time, the sitemap
    always stays in sync with the site: adding a page to the registry is the
    only step required for it to appear here.
    """
    url_entries = []
    for page in PUBLIC_PAGES:
        lastmod = _last_modified(page).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        url_entries.append(
            "  <url>\n"
            f"    <loc>{escape(canonical_url(page['path']))}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>{page['changefreq']}</changefreq>\n"
            f"    <priority>{page['priority']}</priority>\n"
            "  </url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(url_entries)
        + "\n</urlset>\n"
    )
    return xml


# ── Schema.org JSON-LD ──────────────────────────────────────────────────────
def jsonld_website() -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": f"{SITE_URL}/#website",
        "name": SITE_NAME,
        "url": SITE_URL,
        "description": SITE_DESCRIPTION,
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "inLanguage": "en",
    }


def jsonld_organization() -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": f"{SITE_URL}/#organization",
        "name": SITE_NAME,
        "url": SITE_URL,
        "logo": {"@type": "ImageObject", "url": LOGO_URL, "width": 426, "height": 176},
        "image": OG_IMAGE_URL,
        "description": SITE_DESCRIPTION,
        "email": "support@valleymind.ai",
        "sameAs": SAME_AS,
    }


def jsonld_webpage(page: dict) -> dict:
    url = canonical_url(page["path"])
    return {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "@id": f"{url}#webpage",
        "url": url,
        "name": page["title"],
        "description": page["description"],
        "isPartOf": {"@id": f"{SITE_URL}/#website"},
        "inLanguage": "en",
    }


def jsonld_breadcrumbs(page: dict) -> dict:
    url = canonical_url(page["path"])
    items = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE_URL},
        {"@type": "ListItem", "position": 2, "name": page["title"], "item": url},
    ]
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": items,
    }


def jsonld_for(page: dict) -> list:
    """The full JSON-LD block set for a server-rendered page."""
    return [
        jsonld_website(),
        jsonld_organization(),
        jsonld_webpage(page),
        jsonld_breadcrumbs(page),
    ]


# ── Page rendering ──────────────────────────────────────────────────────────
def render_page(key: str):
    """Render a public marketing page with the complete SEO head.

    The context passed to the template drives every important tag:
    title, description, canonical, Open Graph, Twitter card and the JSON-LD
    array that base.html serialises into <script type="application/ld+json">.
    """
    page = _page(key)
    url = canonical_url(page["path"])
    context = {
        "site_name": SITE_NAME,
        "site_url": SITE_URL,
        "page_title": page["title"],
        "page_description": page["description"],
        "canonical": url,
        "og_type": "website",
        "og_image": OG_IMAGE_URL,
        "og_image_alt": f"{SITE_NAME} preview",
        "twitter_site": "@valleymindai",
        "active_page": page["path"],
        "nav_links": NAV_LINKS,
        "jsonld": jsonld_for(page),
    }
    return render_template(page["template"], **context)
