"""
rate_limiter.py — Sliding-window rate limiter, per user_id, in-memory.

Limits (por ventana de 60s):
  Autenticado   → 20 req/min
  Sin auth      → 5 req/min
  Dev mode (sin SUPABASE_URL ni SUPABASE_JWT_SECRET) → sin límite
"""

import os
from collections import deque
from threading import Lock
from datetime import datetime, timedelta

_LIMIT_AUTH  = 20
_LIMIT_ANON  = 5
_WINDOW_SECS = 60
_MAX_BUCKETS = 500   # poda de buckets vacíos al superar este tamaño

_buckets: dict[str, deque] = {}
_lock = Lock()

# Producción = hay Supabase configurado (URL para ES256/JWKS, o secret para
# HS256 legacy). Antes solo se miraba el secret → proyectos nuevos de
# Supabase (asimétricos, sin secret) quedaban SIN rate limit en producción.
_PRODUCCION = bool(os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_JWT_SECRET"))


def check(user_id: str) -> tuple[bool, str]:
    """
    Verifica si el usuario puede hacer otro request.
    Retorna (allowed, mensaje_error).
    """
    if not _PRODUCCION:
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

        # Evitar crecimiento sin límite del dict con user_ids viejos
        if len(_buckets) > _MAX_BUCKETS:
            vacios = [uid for uid, b in _buckets.items()
                      if uid != user_id and (not b or b[-1] < cutoff)]
            for uid in vacios:
                del _buckets[uid]

        return True, ""
