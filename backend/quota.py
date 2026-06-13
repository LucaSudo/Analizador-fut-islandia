"""
quota.py — Límites diarios por usuario (plan gratuito).

Free tier:
  - 2 análisis por día
  - 1 combinada por día
Almacenado en Supabase (tabla uso_diario).
"""

import os
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supabase_client import db

# Usuarios admin: sin límites. Separados por coma en ADMIN_USER_IDS.
_ADMIN_IDS = {uid.strip() for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()}

LIMITE_ANALISIS   = 2
LIMITE_COMBINADAS = 1

_PRODUCCION = bool(os.getenv("SUPABASE_URL"))

_MSG_LOGIN = (
    "⚠️ Necesitás iniciar sesión para usar el análisis.\n"
    "Registrate gratis y obtené 2 análisis y 1 combinada por día."
)

_MSG_LIMITE = (
    "⚠️ Llegaste al límite del plan gratuito para hoy "
    "({limite} {tipo} por día).\n"
    "Tu cupo se renueva automáticamente a medianoche 🕛\n\n"
    "¿Querés análisis ilimitados? Plan Pro — próximamente 🚀"
)


def _get_uso(user_id: str) -> dict:
    today = date.today().isoformat()
    try:
        res = (db.table("uso_diario")
                 .select("analisis, combinadas")
                 .eq("user_id", user_id)
                 .eq("fecha", today)
                 .execute())
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"⚠️  Supabase error (quota read): {e}")
    return {"analisis": 0, "combinadas": 0}


def _upsert_uso(user_id: str, analisis: int, combinadas: int):
    today = date.today().isoformat()
    try:
        db.table("uso_diario").upsert({
            "user_id":    user_id,
            "fecha":      today,
            "analisis":   analisis,
            "combinadas": combinadas,
        }).execute()
    except Exception as e:
        print(f"⚠️  Supabase error (quota write): {e}")


def check_analisis(user_id: str) -> tuple[bool, str]:
    """Retorna (permitido, mensaje). Consume el cupo si está permitido."""
    if user_id in _ADMIN_IDS:
        return True, ""  # Admin: sin límites
    if user_id == "default":
        if _PRODUCCION:
            return False, _MSG_LOGIN
        return True, ""
    uso = _get_uso(user_id)
    if uso.get("analisis", 0) >= LIMITE_ANALISIS:
        return False, _MSG_LIMITE.format(limite=LIMITE_ANALISIS, tipo="análisis")
    # Consumir cupo inmediatamente para evitar carreras
    _upsert_uso(user_id, uso.get("analisis", 0) + 1, uso.get("combinadas", 0))
    return True, ""


def check_combinada(user_id: str) -> tuple[bool, str]:
    """Retorna (permitido, mensaje). Consume el cupo si está permitido."""
    if user_id in _ADMIN_IDS:
        return True, ""  # Admin: sin límites
    if user_id == "default":
        if _PRODUCCION:
            return False, _MSG_LOGIN
        return True, ""
    uso = _get_uso(user_id)
    if uso.get("combinadas", 0) >= LIMITE_COMBINADAS:
        return False, _MSG_LIMITE.format(limite=LIMITE_COMBINADAS, tipo="combinada")
    _upsert_uso(user_id, uso.get("analisis", 0), uso.get("combinadas", 0) + 1)
    return True, ""


def devolver_analisis(user_id: str):
    """Devuelve un cupo de análisis consumido (ej: el análisis falló por
    falta de datos). No aplica a admin ni anónimos (no consumen cupo real)."""
    if user_id in _ADMIN_IDS or user_id == "default":
        return
    uso = _get_uso(user_id)
    _upsert_uso(user_id, max(0, uso.get("analisis", 0) - 1), uso.get("combinadas", 0))


def devolver_combinada(user_id: str):
    """Devuelve un cupo de combinada consumido (ej: no se generaron picks)."""
    if user_id in _ADMIN_IDS or user_id == "default":
        return
    uso = _get_uso(user_id)
    _upsert_uso(user_id, uso.get("analisis", 0), max(0, uso.get("combinadas", 0) - 1))


def get_estado(user_id: str) -> dict:
    """Estado de cuota del día SIN consumir cupo. Para mostrar en la UI.

    {analisis_usados, analisis_limite, combinadas_usados, combinadas_limite,
     ilimitado}. Admin → ilimitado=True. Anónimo ('default') → usados 0.
    """
    if user_id in _ADMIN_IDS:
        return {
            "analisis_usados": 0, "analisis_limite": LIMITE_ANALISIS,
            "combinadas_usados": 0, "combinadas_limite": LIMITE_COMBINADAS,
            "ilimitado": True,
        }
    if user_id == "default":
        return {
            "analisis_usados": 0, "analisis_limite": LIMITE_ANALISIS,
            "combinadas_usados": 0, "combinadas_limite": LIMITE_COMBINADAS,
            "ilimitado": False,
        }
    uso = _get_uso(user_id)
    return {
        "analisis_usados":   uso.get("analisis", 0),
        "analisis_limite":   LIMITE_ANALISIS,
        "combinadas_usados": uso.get("combinadas", 0),
        "combinadas_limite": LIMITE_COMBINADAS,
        "ilimitado":         False,
    }
