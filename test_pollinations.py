import urllib.request, urllib.error, socket
socket.setdefaulttimeout(10)
try:
    req = urllib.request.Request(
        "https://image.pollinations.ai/prompt/test",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    resp = urllib.request.urlopen(req)
    data = resp.read()
    print(f"status: {resp.status}")
    print(f"type: {resp.headers.get('content-type', 'unknown')}")
    print(f"size: {len(data)}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
