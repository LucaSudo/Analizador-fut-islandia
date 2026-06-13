"""
session_store.py — Chat history per session, backed by Supabase.

Write-through: every change is persisted immediately.
In-memory cache for fast reads within the same server instance.
Sessions survive server restarts.

Ownership: cada sesión tiene un user_id dueño. Las funciones reciben el
user_id del request y NUNCA leen/escriben sesiones de otro usuario.
Una sesión anónima ("default") puede ser reclamada por el primer usuario
autenticado que la use. get_history devuelve una COPIA: los callers no
deben asumir que ven mutaciones posteriores del historial.
"""

import sys
import os
from threading import Lock
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supabase_client import db

_cache: dict = {}
_lock = Lock()
SESSION_TTL_HOURS = 24


def _load_from_db(session_id: str) -> tuple[list, str]:
    """Returns (history, user_id) from Supabase, or ([], 'default') if not found."""
    try:
        res = (db.table("sesiones")
                 .select("history, user_id")
                 .eq("session_id", session_id)
                 .execute())
        if res.data:
            row = res.data[0]
            return row.get("history") or [], row.get("user_id") or "default"
    except Exception as e:
        print(f"⚠️  Supabase error (load session): {e}")
    return [], "default"


def _save_to_db(session_id: str, history: list, user_id: str):
    try:
        db.table("sesiones").upsert({
            "session_id":  session_id,
            "user_id":     user_id,
            "history":     history,
            "last_access": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        print(f"⚠️  Supabase error (save session): {e}")


def _ensure_loaded(session_id: str, user_id: str) -> dict | None:
    """Carga la sesión en cache (desde DB si hace falta) y aplica ownership.
    Retorna el dict de sesión si el caller es el dueño (o la reclama),
    o None si pertenece a otro usuario. Llamar con _lock tomado."""
    s = _cache.get(session_id)
    if s is None:
        history, stored_uid = _load_from_db(session_id)
        s = {"history": history, "user_id": stored_uid, "last_access": datetime.now()}
        _cache[session_id] = s
    s["last_access"] = datetime.now()
    if s["user_id"] == "default" and user_id != "default":
        s["user_id"] = user_id  # reclamar sesión anónima
    if s["user_id"] != "default" and s["user_id"] != user_id:
        return None  # sesión de otro usuario
    return s


def get_history(session_id: str, user_id: str = "default") -> list:
    """Devuelve una COPIA del historial, o [] si la sesión es de otro usuario."""
    with _lock:
        s = _ensure_loaded(session_id, user_id)
        return list(s["history"]) if s else []


def append_message(session_id: str, role: str, content: str,
                   user_id: str = "default"):
    with _lock:
        s = _ensure_loaded(session_id, user_id)
        if s is None:
            return
        s["history"].append({"role": role, "content": content})
        history_copy = list(s["history"])
        uid = s["user_id"]
    _save_to_db(session_id, history_copy, uid)


def replace_last_assistant(session_id: str, content: str,
                           user_id: str = "default"):
    with _lock:
        s = _ensure_loaded(session_id, user_id)
        if s is None:
            return
        for i in range(len(s["history"]) - 1, -1, -1):
            if s["history"][i]["role"] == "assistant":
                s["history"][i]["content"] = content
                break
        history_copy = list(s["history"])
        uid = s["user_id"]
    _save_to_db(session_id, history_copy, uid)


def clear_session(session_id: str, user_id: str = "default") -> bool:
    """Borra la sesión solo si pertenece al caller (o es anónima).
    Retorna True si se borró."""
    with _lock:
        s = _cache.get(session_id)
        owner = s["user_id"] if s else _load_from_db(session_id)[1]
        if owner != "default" and owner != user_id:
            return False
        _cache.pop(session_id, None)
    try:
        db.table("sesiones").delete().eq("session_id", session_id).execute()
    except Exception as e:
        print(f"⚠️  Supabase error (clear session): {e}")
    return True


def cleanup_expired() -> int:
    cutoff_str = (datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    local_cutoff = datetime.now() - timedelta(hours=SESSION_TTL_HOURS)
    with _lock:
        expired = [k for k, v in _cache.items() if v["last_access"] < local_cutoff]
        for k in expired:
            del _cache[k]
    try:
        db.table("sesiones").delete().lt("last_access", cutoff_str).execute()
    except Exception as e:
        print(f"⚠️  Supabase error (cleanup sessions): {e}")
    return len(expired)
