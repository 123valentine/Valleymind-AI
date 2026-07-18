import os


bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
# Video generation (wan2.7) streams for 5-10 min; the worker must not be reaped
# mid-generation. Set to 900s to match VIDEO_MAX_POLL_SECONDS. (The download +
# GridFS save now run in a background thread that outlives the request, so the
# request itself no longer has to survive the full generation — but a generous
# timeout keeps the progress-streaming SSE connection alive meanwhile.)
# Render allows responses up to 100 min, so the platform is not the limit.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "900"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))
