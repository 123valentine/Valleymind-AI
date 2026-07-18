import os


bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
# Video generation (wan2.7) streams for 5-10 min on a single request; the
# worker must not be reaped mid-generation. Default raised to 1200s (20 min) to
# cover the 900s poll ceiling plus MP4 download + GridFS save, with headroom.
# Override with GUNICORN_TIMEOUT.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "1200"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))
