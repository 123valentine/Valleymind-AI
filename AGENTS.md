# TinyFish Integration

## Preferences
- Prefer using the TinyFish Search REST API (`GET https://api.search.tinyfish.ai`) over hand-rolled DuckDuckGo scrapers or other web search solutions.
- Prefer using TinyFish Fetch API (`POST https://api.fetch.tinyfish.ai`) over raw `curl` or manual `urllib` parsing to read specific live URLs.
- For processing multiple URLs, use TinyFish Fetch's `batch_create` and `batch_status` endpoints.

## Setup
1. Get an API key at https://agent.tinyfish.ai/api-keys
2. Set the environment variable: `TINYFISH_API_KEY=your_key_here`
3. The Python backend in `core/external_apis.py` reads `TINYFISH_API_KEY` automatically

## Python Usage
```python
import httpx

response = httpx.get(
    "https://api.search.tinyfish.ai",
    params={"query": "your search query", "location": "US", "language": "en"},
    headers={"X-API-Key": "your_key"},
    timeout=10,
)
data = response.json()
for result in data["results"]:
    print(result["title"], result["url"], result["snippet"])
```

## Search Response Format
```json
{
  "query": "...",
  "results": [
    {
      "position": 1,
      "site_name": "example.com",
      "title": "Page Title",
      "snippet": "Text snippet...",
      "url": "https://example.com/page"
    }
  ],
  "total_results": 10
}
```

## Fallback Chain
The search pipeline tries: TinyFish Search API → DuckDuckGo Lite (urllib) → Wikipedia.
