"""
cache_manager.py — Cache de datos de SofaScore en Supabase.

Stats de partidos terminados → se guardan para siempre (nunca cambian).
Lista de partidos recientes por equipo → TTL de 6 horas.

Beneficio: drástica reducción de llamadas al proxy.
  - 1ra vez que alguien analiza Real Madrid: ~10 llamadas al proxy.
  - 2da vez (mismo día u otro usuario): 0 llamadas al proxy.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supabase_client import db

PARTIDOS_TTL_HORAS = 6


# ── Stats de un partido terminado ────────────────────────────────────

def get_stats_partido(evento_id: int) -> dict | None:
    """Retorna stats cacheadas de un partido terminado, o None si no hay cache."""
    try:
        res = (db.table("stats_partidos_cache")
                 .select("stats")
                 .eq("evento_id", evento_id)
                 .execute())
        if res.data:
            return res.data[0]["stats"]
    except Exception as e:
        print(f"⚠️  Cache read error (stats_partido {evento_id}): {e}")
    return None


def set_stats_partido(evento_id: int, stats: dict):
    """Guarda stats de un partido terminado en Supabase (sin expiración)."""
    try:
        db.table("stats_partidos_cache").upsert({
            "evento_id": evento_id,
            "stats":     stats,
        }).execute()
    except Exception as e:
        print(f"⚠️  Cache write error (stats_partido {evento_id}): {e}")


# ── Lista de partidos recientes de un equipo ─────────────────────────

def _cache_key(equipo: str, liga_id: int, temporada_id: int) -> str:
    return f"{equipo.lower().replace(' ', '_')}_{liga_id}_{temporada_id}"


def get_partidos_equipo(equipo: str, liga_id: int, temporada_id: int) -> list | None:
    """Retorna lista de partidos cacheada (válida 6h), o None si expiró/no existe."""
    key = _cache_key(equipo, liga_id, temporada_id)
    try:
        res = (db.table("partidos_equipo_cache")
                 .select("partidos, updated_at")
                 .eq("id", key)
                 .execute())
        if res.data:
            row = res.data[0]
            updated_str = row["updated_at"]
            # Normalizar timezone
            if updated_str.endswith("Z"):
                updated_str = updated_str[:-1] + "+00:00"
            updated = datetime.fromisoformat(updated_str)
            now_utc = datetime.now(timezone.utc)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if now_utc - updated < timedelta(hours=PARTIDOS_TTL_HORAS):
                return row["partidos"]
    except Exception as e:
        print(f"⚠️  Cache read error (partidos {equipo}): {e}")
    return None


def set_partidos_equipo(equipo: str, liga_id: int, temporada_id: int, partidos: list):
    """Guarda lista de partidos en Supabase con TTL de 6 horas."""
    key = _cache_key(equipo, liga_id, temporada_id)
    try:
        db.table("partidos_equipo_cache").upsert({
            "id":         key,
            "partidos":   partidos,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"⚠️  Cache write error (partidos {equipo}): {e}")
