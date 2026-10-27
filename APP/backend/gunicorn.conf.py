# Gunicorn configuration for SprinzlMap
# Usage: gunicorn -c gunicorn.conf.py app:app

import os


def _env_int(name: str, default: int, min_v: int, max_v: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        v = int(raw)
    except Exception:
        return default
    return max(min_v, min(v, max_v))


# Process-level concurrency. Keep >1 to reduce head-of-line blocking.
# Can be overridden by env: GUNICORN_WORKERS
workers = _env_int("GUNICORN_WORKERS", 2, 1, 16)

# Thread-level concurrency for I/O-heavy requests.
# Can be overridden by env: GUNICORN_THREADS
threads = _env_int("GUNICORN_THREADS", 4, 1, 64)

# Explicit thread worker class.
worker_class = "gthread"

# Worker timeout in seconds (requests running longer will be killed).
timeout = 1200


# Graceful shutdown timeout (seconds). Controls how long workers have to
# finish ongoing requests after a SIGINT/SIGTERM before being killed.
graceful_timeout = 5

# Exit workers immediately on SIGINT (Ctrl+C) for faster shutdown.
# Note: requires Gunicorn 20+.
worker_exit_on_sigint = True

# Bind address: host:port
# Default to all interfaces because the public frontend currently proxies to
# this backend over the LAN address 192.168.236.2:5679.
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5679")

# PID file for external stop/restart scripts.
pidfile = "gunicorn.pid"

# Access log output. "-" means stdout.
accesslog = "-"

# (Optional) Error log output. "-" means stderr.
# errorlog = "-"

# (Optional) Log level: debug, info, warning, error, critical
# loglevel = "info"

# (Optional) Keep-alive for HTTP connections (seconds)
# keepalive = 2

# (Optional) Max requests per worker before restart (helps with memory leaks)
# max_requests = 0
# max_requests_jitter = 0
