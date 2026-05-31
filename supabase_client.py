"""
supabase_client.py — Singleton del cliente Supabase.
Importar `db` desde acá en cualquier módulo que necesite acceder a la BD.
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_url: str = os.getenv("SUPABASE_URL", "")
# Soporta tanto SERVICE_ROLE_KEY (backend con bypass RLS) como SUPABASE_KEY
_key: str = os.getenv("SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")

if not _url or not _key:
    raise EnvironmentError(
        "Faltan SUPABASE_URL y SERVICE_ROLE_KEY (o SUPABASE_KEY) en el archivo .env"
    )

db: Client = create_client(_url, _key)
