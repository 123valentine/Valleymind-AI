"""One real, small request to TinyFish Search API to discover which fields it returns.

Guarded by TINYFISH_API_KEY: if the key is not configured, reports a skip
instead of failing. This is read-only (GET only) and uses a tiny num=1 query.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

api_key = os.getenv("TINYFISH_API_KEY", "").strip()
if not api_key:
    print("TINYFISH_PROBE: SKIP - TINYFISH_API_KEY not configured; cannot discover live fields.")
    print("TINYFISH_PROBE: OK (skip)")
    sys.exit(0)

try:
    import requests
except Exception as exc:
    print("TINYFISH_PROBE: SKIP - requests unavailable:", exc)
    sys.exit(0)

try:
    resp = requests.get(
        "https://api.search.tinyfish.ai",
        headers={"X-API-Key": api_key},
        params={"query": "latest iPhone model", "num": 1, "fast": True},
        timeout=15,
    )
    print("TINYFISH_PROBE: HTTP", resp.status_code)
    try:
        data = resp.json()
    except Exception:
        print("TINYFISH_PROBE: non-JSON body:", resp.text[:500])
        sys.exit(0)
    results = data.get("results") or []
    print("TINYFISH_PROBE: result_count=", len(results))
    if results:
        print("TINYFISH_PROBE: top-level keys:", sorted(data.keys()))
        print("TINYFISH_PROBE: result[0] keys:", sorted(results[0].keys()))
        print("TINYFISH_PROBE: result[0]:", json.dumps(results[0], ensure_ascii=False)[:1200])
        has_image = any(k in results[0] for k in ("image", "thumbnail", "image_url", "og_image", "media"))
        print("TINYFISH_PROBE: offers_image_fields=", bool(has_image))
    else:
        print("TINYFISH_PROBE: no results returned")
    print("TINYFISH_PROBE: OK")
except Exception as exc:
    print("TINYFISH_PROBE: request failed:", exc)
    sys.exit(1)
