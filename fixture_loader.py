import os
import sys
from curl_cffi import requests as cf_requests
from datetime import datetime, date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(_HERE, "backend")
for _p in (_BACKEND, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import cache_manager as _cm
except ImportError:
    _cm = None

# Offset horario de referencia (en horas desde UTC).
# Default: -3 (Argentina). Ajustable via env var APP_TZ_OFFSET.
_TZ_OFFSET = int(os.getenv("APP_TZ_OFFSET", "-3"))

def _hoy_local() -> date:
    """Fecha 'hoy' en la zona horaria configurada (no UTC del servidor)."""
    return (datetime.utcnow() + timedelta(hours=_TZ_OFFSET)).date()

def _inicio_hoy_utc() -> float:
    """Timestamp UTC del inicio del día local (medianoche local expresada en UTC)."""
    hoy = _hoy_local()
    return datetime(hoy.year, hoy.month, hoy.day).timestamp() - _TZ_OFFSET * 3600

LIGAS_CONFIG = {
    "Besta deild karla": 188,
    "1. deild karla": 675,
    "La Liga": 8,
    "Premier League": 17,
    "Serie A": 23,
    "Bundesliga": 35,
    "Ligue 1": 34,
    "Ligue 2": 182,
    "Champions League": 7,
    "Liga 1 Perú": 406,
    "Copa Libertadores": 384,
    "Copa Sudamericana": 480,
    "Saudi Pro League": 955,
    # Liga Profesional Argentina (Primera División). Si el ID falla,
    # obtener_temporadas_actuales() la ignora silenciosamente y el resto
    # del sistema sigue funcionando.
    "Liga Profesional Argentina": 155,
}
LIGAS = {}

def _nueva_sesion():
    session = cf_requests.Session(impersonate="chrome124")
    proxy_url = os.getenv("PROXY_URL", "")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session

def fetch_api(sesion, url):
    return sesion.get(url, timeout=15).json()

def obtener_temporadas_actuales(sesion, forzar_refresh: bool = False):
    global LIGAS

    if not forzar_refresh and _cm is not None:
        cached = _cm.get_ligas()
        if cached:
            print("[cache] LIGAS → hit (saltando 28 llamadas al proxy)")
            LIGAS = cached
            return cached

    ligas = {}
    for nombre, liga_id in LIGAS_CONFIG.items():
        try:
            data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/seasons")
            temporadas = data.get("seasons", [])
            if temporadas:
                temporada_id = temporadas[0]["id"]
                rounds_data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/season/{temporada_id}/rounds")
                rondas = rounds_data.get("rounds", [])
                ronda_actual = (
                    rounds_data.get("currentRound", {}).get("round")
                    or (rondas[-1].get("round", 1) if rondas else 1)
                )
                ligas[nombre] = {
                    "id": liga_id,
                    "temporada": temporada_id,
                    "rondas": ronda_actual
                }
        except:
            pass
    LIGAS = ligas
    if _cm is not None and ligas:
        _cm.set_ligas(ligas)
        print("[cache] LIGAS → guardado en Supabase (TTL 24h)")
    return ligas

def cargar_proximos_partidos(forzar_refresh: bool = False):
    global LIGAS

    # ── Caché check ──────────────────────────────────────────────────
    if not forzar_refresh and _cm is not None:
        cached = _cm.get_fixtures_texto()
        if cached:
            print("[cache] fixtures → hit (saltando ~60 llamadas al proxy)")
            if not LIGAS:
                try:
                    sesion = _nueva_sesion()
                    LIGAS = obtener_temporadas_actuales(sesion)
                except Exception as e:
                    print(f"[cache] fixtures hit pero LIGAS falló: {e}")
            return cached

    contexto = "=== PRÓXIMOS PARTIDOS POR LIGA ===\n"
    ahora = datetime.now().timestamp()
    inicio_hoy = _inicio_hoy_utc()
    hoy_local   = _hoy_local()

    sesion = _nueva_sesion()
    LIGAS = obtener_temporadas_actuales(sesion)

    # ── Paso extra: buscar por fecha para capturar fases de grupos ──────────
    id_a_nombre = {v: k for k, v in LIGAS_CONFIG.items()}
    partidos_por_fecha: dict[str, list] = {}
    for delta in range(5):
        fecha_str_api = (hoy_local + timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            resp_fecha = fetch_api(
                sesion,
                f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{fecha_str_api}"
            )
            for evento in resp_fecha.get("events", []):
                torneo_id = (evento.get("tournament", {})
                                   .get("uniqueTournament", {})
                                   .get("id"))
                if torneo_id in id_a_nombre:
                    nombre = id_a_nombre[torneo_id]
                    partidos_por_fecha.setdefault(nombre, []).append(evento)
        except:
            pass

    for nombre_liga, datos in LIGAS.items():
        try:
            candidatos = []
            base = (f"https://www.sofascore.com/api/v1/unique-tournament"
                    f"/{datos['id']}/season/{datos['temporada']}/events")
            for endpoint in ["last/0", "next/0"]:
                try:
                    resp = fetch_api(sesion, f"{base}/{endpoint}")
                    candidatos.extend(resp.get("events", []))
                except:
                    pass

            candidatos.extend(partidos_por_fecha.get(nombre_liga, []))

            vistos = set()
            eventos = []
            for e in sorted(candidatos, key=lambda x: x.get("startTimestamp", 0)):
                eid  = e["id"]
                tipo = e.get("status", {}).get("type", "")
                ts   = e.get("startTimestamp", 0)
                es_hoy    = ts >= inicio_hoy and ts < inicio_hoy + 86400
                es_futuro = (
                    tipo == "inprogress"
                    or (tipo == "notstarted" and ts > ahora)
                    or (tipo == "notstarted" and es_hoy)
                )
                if es_futuro and eid not in vistos:
                    vistos.add(eid)
                    eventos.append(e)

            if eventos:
                contexto += f"\n{nombre_liga}:\n"
                hoy_str = hoy_local.strftime("%d/%m/%Y")
                for e in eventos:
                    home = e["homeTeam"]["name"]
                    away = e["awayTeam"]["name"]
                    ts_e = e.get("startTimestamp", "")
                    tipo_e = e.get("status", {}).get("type", "")
                    if ts_e:
                        # #0m: Formatear SIEMPRE en UTC. La conversión al
                        # timezone del usuario la hace engine._retag_fixtures_para_tz()
                        # con el offset correcto. datetime.fromtimestamp() depende
                        # del OS del servidor → inconsistente entre dev y Render.
                        fecha_str = datetime.utcfromtimestamp(ts_e).strftime("%d/%m/%Y %H:%M")
                        # Compara contra "hoy" en TZ del server para etiqueta inicial;
                        # _retag_fixtures_para_tz lo recalcula según user_tz.
                        if fecha_str.startswith(hoy_str):
                            fecha_str += " [HOY]"
                    else:
                        fecha_str = "por confirmar"
                    estado_str = " [EN CURSO]" if tipo_e == "inprogress" else ""
                    contexto += f"  - {home} vs {away} ({fecha_str}{estado_str})\n"
        except:
            pass

    # ── Guardar en caché para los próximos cold starts ───────────────
    if _cm is not None:
        _cm.set_fixtures_texto(contexto)
        print("[cache] fixtures → guardado en Supabase (TTL 2h)")

    return contexto
