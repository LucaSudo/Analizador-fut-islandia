"""
session_store.py — Chat history per session, backed by Supabase.

Write-through: every change is persisted immediately.
In-memory cache for fast reads within the same server instance.
Sessions survive server restarts.
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


def get_history(session_id: str, user_id: str = "default") -> list:
    with _lock:
        if session_id not in _cache:
            history, stored_uid = _load_from_db(session_id)
            resolved_uid = user_id if user_id != "default" else stored_uid
            _cache[session_id] = {
                "history":     history,
                "user_id":     resolved_uid,
                "last_access": datetime.now(),
            }
        else:
            s = _cache[session_id]
            s["last_access"] = datetime.now()
            if user_id != "default":
                s["user_id"] = user_id
        return _cache[session_id]["history"]


def append_message(session_id: str, role: str, content: str):
    with _lock:
        s = _cache.setdefault(session_id, {
            "history":     [],
            "user_id":     "default",
            "last_access": datetime.now(),
        })
        s["history"].append({"role": role, "content": content})
        s["last_access"] = datetime.now()
        history_copy = list(s["history"])
        uid = s["user_id"]
    _save_to_db(session_id, history_copy, uid)


def replace_last_assistant(session_id: str, content: str):
    with _lock:
        s = _cache.get(session_id)
        if not s:
            return
        for i in range(len(s["history"]) - 1, -1, -1):
            if s["history"][i]["role"] == "assistant":
                s["history"][i]["content"] = content
                break
        history_copy = list(s["history"])
        uid = s["user_id"]
    _save_to_db(session_id, history_copy, uid)


def clear_session(session_id: str):
    with _lock:
        _cache.pop(session_id, None)
    try:
        db.table("sesiones").delete().eq("session_id", session_id).execute()
    except Exception as e:
        print(f"⚠️  Supabase error (clear session): {e}")


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
