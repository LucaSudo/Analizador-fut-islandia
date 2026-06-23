"""
stats_colectivas.py — Estadísticas agregadas anónimas de predicciones verificadas.

Cachea en memoria stats de Supabase y las refresca cada 2 horas.
No filtra por user_id: usa datos de todos los usuarios de forma agregada.
"""
import re
import threading
from datetime import datetime

# ── Buckets de línea por foco ────────────────────────────────────────────────
_BUCKETS: dict[str, list[float]] = {
    "corners":               [6.5, 7.5, 8.5, 9.5, 10.5, 11.5],
    "corners_1h":            [3.5, 4.5, 5.5, 6.5],
    "corners_2h":            [3.5, 4.5, 5.5, 6.5],
    "goles":                 [1.5, 2.5, 3.5, 4.5],
    "goles_1h":              [0.5, 1.5, 2.5],
    "goles_2h":              [0.5, 1.5, 2.5],
    "tarjetas_amarillas":    [2.5, 3.5, 4.5, 5.5],
    "tarjetas_amarillas_1h": [1.5, 2.5, 3.5],
    "tarjetas_amarillas_2h": [1.5, 2.5, 3.5],
    "tarjetas_rojas":        [0.5, 1.5],
    "remates":               [8.5, 10.5, 12.5],
    "remates_1h":            [4.5, 6.5, 8.5],
    "remates_2h":            [4.5, 6.5, 8.5],
    "faltas":                [18.5, 22.5, 26.5],
    "faltas_1h":             [8.5, 11.5, 14.5],
    "faltas_2h":             [8.5, 11.5, 14.5],
}

_RE_LINEA = re.compile(r'Over\s+(\d+(?:\.\d+)?)', re.IGNORECASE)
_RE_BTTS  = re.compile(r'Ambos\s+[Aa]notan?\s+(Sí|Si|No)', re.IGNORECASE)
_RE_1X2   = re.compile(r'(local\s+gana|visitante\s+gana|empate)', re.IGNORECASE)
_RE_DOBLE = re.compile(r'\b(1X|X2|12)\b')

CACHE_TTL_HOURS = 2
_cache_stats: dict | None = None
_cache_ts:    datetime | None = None
_cache_lock   = threading.Lock()
_timer: threading.Timer | None = None

# ── Umbrales mínimos de muestras ─────────────────────────────────────────────
_MIN_MUESTRAS_A  = 5
_MIN_MUESTRAS_BC = 10


# ── Helpers internos ─────────────────────────────────────────────────────────

def _acumular(d: dict, key: str, acerto: bool) -> None:
    if key not in d:
        d[key] = {"muestras": 0, "aciertos": 0}
    d[key]["muestras"] += 1
    if acerto:
        d[key]["aciertos"] += 1


def _extraer_rango(foco: str, prediccion: str) -> str | None:
    """Extrae el rango/valor predicho del texto de predicción y lo snappea al bucket más cercano."""
    if foco in ("btts",):
        m = _RE_BTTS.search(prediccion)
        if m:
            val = "Sí" if m.group(1).lower() in ("sí", "si") else "No"
            return f"Ambos Anotan {val}"
        return None

    if foco in ("1x2", "ganador"):
        m = _RE_1X2.search(prediccion)
        return m.group(1).title() if m else None

    if foco == "doble_oportunidad":
        m = _RE_DOBLE.search(prediccion)
        return m.group(1) if m else None

    # Numérico: extraer "Over X.5" y snappear al bucket más cercano
    m = _RE_LINEA.search(prediccion)
    if not m:
        return None
    linea_val = float(m.group(1))
    foco_base = foco.split("_")[0] if "_" in foco else foco
    buckets = _BUCKETS.get(foco, _BUCKETS.get(foco_base, []))
    if not buckets:
        return f"Over {linea_val}"
    closest = min(buckets, key=lambda b: abs(b - linea_val))
    return f"Over {closest}"


def _build_liga_reverse_map() -> dict:
    """Construye {liga_id (int): liga_nombre (str)} desde fixture_loader."""
    import sys, os
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    try:
        from fixture_loader import LIGAS_CONFIG
        return {v: k for k, v in LIGAS_CONFIG.items()}
    except Exception as e:
        print(f"⚠️  stats_colectivas: no se pudo cargar LIGAS_CONFIG: {e}")
        return {}


def _calcular_stats() -> dict:
    """Lee todas las predicciones verificadas de Supabase y agrupa por foco / foco+liga / foco+liga+rango."""
    from supabase_client import db

    try:
        res = (db.table("predicciones")
                 .select("foco, prediccion, acerto, liga_id")
                 .not_.is_("acerto", "null")
                 .execute())
        preds = res.data or []
    except Exception as e:
        print(f"⚠️  stats_colectivas: error cargando predicciones: {e}")
        return {}

    liga_id_a_nombre   = _build_liga_reverse_map()
    por_foco: dict          = {}
    por_foco_liga: dict     = {}
    por_foco_liga_rango: dict = {}

    for p in preds:
        foco       = p.get("foco", "")
        acerto     = p.get("acerto")
        liga_id    = p.get("liga_id")
        prediccion = p.get("prediccion", "")

        if not foco or acerto is None:
            continue

        liga_nombre = liga_id_a_nombre.get(liga_id) if liga_id else None
        rango       = _extraer_rango(foco, prediccion)

        _acumular(por_foco, foco, acerto)
        if liga_nombre:
            _acumular(por_foco_liga, f"{foco}__{liga_nombre}", acerto)
        if liga_nombre and rango:
            _acumular(por_foco_liga_rango, f"{foco}__{liga_nombre}__{rango}", acerto)

    return {
        "por_foco":            por_foco,
        "por_foco_liga":       por_foco_liga,
        "por_foco_liga_rango": por_foco_liga_rango,
    }


# ── API pública ───────────────────────────────────────────────────────────────

def refresh_stats() -> None:
    """Recalcula el caché. Llama a esto al arrancar y cada 2h."""
    global _cache_stats, _cache_ts, _timer
    with _cache_lock:
        _cache_stats = _calcular_stats()
        _cache_ts    = datetime.utcnow()
        total = sum(v["muestras"] for v in _cache_stats.get("por_foco", {}).values())
        print(f"[stats_colectivas] caché actualizado — {total} predicciones verificadas")
    _timer = threading.Timer(CACHE_TTL_HOURS * 3600, refresh_stats)
    _timer.daemon = True
    _timer.start()


def get_track_record(foco: str, liga: str | None, rango: str | None) -> dict | None:
    """
    Retorna el track record más específico disponible (nivel C → B → A), o None.

    Nivel C: foco + liga + rango  (≥10 muestras)
    Nivel B: foco + liga          (≥10 muestras)
    Nivel A: foco global          (≥5 muestras)
    """
    with _cache_lock:
        stats = _cache_stats
    if not stats:
        return None

    pflr = stats.get("por_foco_liga_rango", {})
    pfl  = stats.get("por_foco_liga", {})
    pf   = stats.get("por_foco", {})

    # Nivel C
    if liga and rango:
        key_c = f"{foco}__{liga}__{rango}"
        if key_c in pflr and pflr[key_c]["muestras"] >= _MIN_MUESTRAS_BC:
            d = pflr[key_c]
            return {"nivel": "C", "foco": foco, "liga": liga, "rango": rango,
                    "muestras": d["muestras"], "aciertos": d["aciertos"],
                    "tasa": d["aciertos"] / d["muestras"]}

    # Nivel B
    if liga:
        key_b = f"{foco}__{liga}"
        if key_b in pfl and pfl[key_b]["muestras"] >= _MIN_MUESTRAS_BC:
            d = pfl[key_b]
            return {"nivel": "B", "foco": foco, "liga": liga, "rango": None,
                    "muestras": d["muestras"], "aciertos": d["aciertos"],
                    "tasa": d["aciertos"] / d["muestras"]}

    # Nivel A
    if foco in pf and pf[foco]["muestras"] >= _MIN_MUESTRAS_A:
        d = pf[foco]
        return {"nivel": "A", "foco": foco, "liga": None, "rango": None,
                "muestras": d["muestras"], "aciertos": d["aciertos"],
                "tasa": d["aciertos"] / d["muestras"]}

    return None


def get_resumen_global() -> str:
    """Texto compacto con el track record global para el system prompt. Solo focos con ≥5 muestras."""
    with _cache_lock:
        stats = _cache_stats
    if not stats:
        return ""

    pf = stats.get("por_foco", {})
    focos_validos = {k: v for k, v in pf.items() if v["muestras"] >= _MIN_MUESTRAS_A}
    if not focos_validos:
        return ""

    total_m = sum(v["muestras"] for v in focos_validos.values())
    total_a = sum(v["aciertos"] for v in focos_validos.values())
    tasa_global = round(total_a / total_m * 100) if total_m else 0

    por_foco_str = " | ".join(
        f"{k} {round(v['aciertos']/v['muestras']*100)}% ({v['muestras']})"
        for k, v in sorted(focos_validos.items(),
                           key=lambda x: x[1]["aciertos"] / x[1]["muestras"],
                           reverse=True)
    )

    return (
        f"=== TRACK RECORD DEL SISTEMA ===\n"
        f"Tasa global: {tasa_global}% ({total_m} predicciones verificadas)\n"
        f"Por foco: {por_foco_str}\n"
    )
