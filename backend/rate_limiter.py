"""
rate_limiter.py — Sliding-window rate limiter, per user_id, in-memory.

Limits (por ventana de 60s):
  Autenticado   → 20 req/min
  Sin auth      → 5 req/min
  Dev mode (sin SUPABASE_JWT_SECRET) → sin límite
"""

import os
from collections import deque
from threading import Lock
from datetime import datetime, timedelta

_LIMIT_AUTH  = 20
_LIMIT_ANON  = 5
_WINDOW_SECS = 60

_buckets: dict[str, deque] = {}
_lock = Lock()

# Si no hay JWT secret configurado, estamos en dev → sin rate limit
_JWT_SECRET_SET = bool(os.getenv("SUPABASE_JWT_SECRET"))


def check(user_id: str) -> tuple[bool, str]:
    """
    Verifica si el usuario puede hacer otro request.
    Retorna (allowed, mensaje_error).
    """
    if not _JWT_SECRET_SET:
        return True, ""

    limit = _LIMIT_ANON if user_id == "default" else _LIMIT_AUTH
    now = datetime.now()
    cutoff = now - timedelta(seconds=_WINDOW_SECS)

    with _lock:
        bucket = _buckets.setdefault(user_id, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            wait = int((bucket[0] + timedelta(seconds=_WINDOW_SECS) - now).total_seconds()) + 1
            return False, f"Demasiadas solicitudes. Esperá {wait} segundos."

        bucket.append(now)
        return True, ""
