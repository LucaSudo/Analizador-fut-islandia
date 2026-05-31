"""
auth.py — Verificación de JWT de Supabase.

Proyectos nuevos usan ES256 (asimétrico, JWKS).
Proyectos legacy usan HS256 (secreto compartido, SUPABASE_JWT_SECRET).
"""

import base64
import json
import os
from dotenv import load_dotenv
from jose import jwt, JWTError

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
_JWT_SECRET   = os.getenv("SUPABASE_JWT_SECRET", "")

_jwks_keys: list | None = None


def _load_jwks() -> list:
    """Carga y cachea las claves públicas del endpoint JWKS de Supabase."""
    global _jwks_keys
    if _jwks_keys is not None:
        return _jwks_keys
    try:
        import httpx
        url = f"{_SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        _jwks_keys = resp.json().get("keys", [])
        print(f"[auth] JWKS cargado: {len(_jwks_keys)} clave(s) de Supabase")
        return _jwks_keys
    except Exception as e:
        print(f"⚠️  Error cargando JWKS de Supabase: {e}")
        _jwks_keys = []
        return []


def _token_header(token: str) -> dict:
    try:
        part = token.split(".")[0]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def verificar_token(authorization: str | None) -> str:
    """
    Recibe el header Authorization ('Bearer <token>').
    Retorna el user_id (sub) si el token es válido.
    Retorna 'default' si no hay token o si no hay configuración (modo dev).
    Lanza ValueError si el token está presente pero es inválido.
    """
    if not _SUPABASE_URL and not _JWT_SECRET:
        return "default"

    if not authorization or not authorization.startswith("Bearer "):
        return "default"

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return "default"

    header = _token_header(token)
    alg = header.get("alg", "HS256")
    kid = header.get("kid")

    try:
        if alg.startswith("ES") or alg.startswith("RS"):
            # Asimétrico — proyectos nuevos de Supabase (ES256, RS256, etc.)
            if not _SUPABASE_URL:
                raise ValueError("SUPABASE_URL no configurado — necesario para ES256/RS256")
            keys = _load_jwks()
            key = (next((k for k in keys if k.get("kid") == kid), None)
                   if kid else (keys[0] if keys else None))
            if not key:
                raise ValueError("Clave pública no encontrada en el JWKS de Supabase")
            payload = jwt.decode(token, key, algorithms=[alg],
                                 options={"verify_aud": False})
        else:
            # Simétrico — proyectos legacy (HS256/HS512)
            if not _JWT_SECRET:
                return "default"
            payload = jwt.decode(token, _JWT_SECRET,
                                 algorithms=["HS256", "HS512"],
                                 options={"verify_aud": False})

        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("Token sin sub claim")
        return user_id

    except JWTError as e:
        raise ValueError(f"Token inválido (alg={alg}): {e}")
