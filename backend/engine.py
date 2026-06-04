"""
engine.py — Core analysis engine, adapted from interfaz.py for API use.
No GUI dependencies. Thread-safe for concurrent requests.

Key differences vs interfaz.py:
  - No CustomTkinter.
  - No global historial: history is per-session via session_store.
  - LIGA_ID / TEMPORADA_ID / RONDAS_TOTALES are local to each request (not global).
  - All long-running functions accept a progress_cb(str) callable for SSE status updates.
  - initialize_engine() must be called once at startup to load fixtures + LIGAS.
"""

import math
import sys
import os
import re
import time
import threading
from datetime import datetime, date, timedelta, timezone

# ── Per-request timezone (thread-local) ─────────────────────────────
# El backend recibe el offset horario del usuario en cada request y lo
# guarda acá. Las funciones que calculan "hoy" lo leen para no depender
# de la TZ del servidor (que en deploy suele ser UTC).
_tls = threading.local()

def set_request_tz_offset(hours: float | None) -> None:
    _tls.tz_offset_hours = hours

def get_tz_offset_hours() -> float:
    """Offset horas desde UTC. Prioridad: TLS del request → env → -3."""
    v = getattr(_tls, "tz_offset_hours", None)
    if v is not None:
        return v
    try:
        return float(os.getenv("APP_TZ_OFFSET", "-3"))
    except ValueError:
        return -3.0

# #0m: Los fixtures iniciales se formatean SIEMPRE en UTC (fixture_loader.py
# usa datetime.utcfromtimestamp). Por lo tanto el offset "base" es 0 (UTC),
# y _retag_fixtures_para_tz aplica el delta para convertir al TZ del user.
# ANTES estaba ligado a APP_TZ_OFFSET y dependía del OS del servidor →
# inconsistente entre dev y Render.
_SERVER_TZ_AT_LOAD: float = 0.0

from dotenv import load_dotenv
load_dotenv()

# ── Path setup ──────────────────────────────────────────────────────
# _HERE = backend/   _ROOT = Analizador_datosF/
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
# Add both directories so imports work regardless of run location
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from curl_cffi import requests as cf_requests
from groq import Groq

import session_store
import cache_manager
from fixture_loader import cargar_proximos_partidos
import fixture_loader as _fl
from memory import guardar_prediccion, generar_contexto_memoria, verificar_predicciones

try:
    import stats_colectivas as _sc
except ImportError:
    _sc = None

# ── Config ──────────────────────────────────────────────────────────

_API_KEY_GROQ = os.getenv("GROQ_API_KEY")
_client = Groq(api_key=_API_KEY_GROQ)

# LIGAS populated at startup (same structure as fixture_loader.LIGAS)
LIGAS: dict = {}

# ── Aliases de liga (#0i) ────────────────────────────────────────────
# Fuente única de verdad: mapeo de cómo los usuarios llaman a las
# ligas → nombres oficiales tal como están en LIGAS / fixtures.
# Ordenado por especificidad (los más específicos primero) para que
# "1. deild" no sea robado por "liga 1", "premier league" antes que
# "premier", etc.
# Cada alias mapea a UNA LISTA de nombres oficiales. Si tiene varios,
# normalizar_liga() devuelve la lista completa para que el caller decida
# (mostrar todas en fixtures, o tomar solo la primera en combinada AUTO).
_LIGA_ALIASES: list[tuple[str, list[str]]] = [
    # ── Islandia ─────────────────────────────────────────────────
    ("besta deild",        ["Besta deild karla"]),
    ("primera islandesa",  ["Besta deild karla"]),
    ("1. deild",           ["1. deild karla"]),
    ("1 deild",            ["1. deild karla"]),
    ("primera deild",      ["1. deild karla"]),
    ("segunda islandesa",  ["1. deild karla"]),
    ("segunda division islandesa",     ["1. deild karla"]),
    ("segunda división islandesa",     ["1. deild karla"]),
    ("islandia",           ["Besta deild karla", "1. deild karla"]),
    ("islandesa",          ["Besta deild karla", "1. deild karla"]),

    # ── Inglaterra ───────────────────────────────────────────────
    ("premier league",     ["Premier League"]),
    ("premier",            ["Premier League"]),
    ("liga inglesa",       ["Premier League"]),
    ("inglesa",            ["Premier League"]),
    ("inglaterra",         ["Premier League"]),
    ("epl",                ["Premier League"]),

    # ── España ───────────────────────────────────────────────────
    ("la liga",            ["La Liga"]),
    ("laliga",             ["La Liga"]),
    ("liga española",      ["La Liga"]),
    ("liga espanola",      ["La Liga"]),
    ("española",           ["La Liga"]),
    ("espanola",           ["La Liga"]),
    ("españa",             ["La Liga"]),
    ("espana",             ["La Liga"]),
    ("primera española",   ["La Liga"]),

    # ── Italia ───────────────────────────────────────────────────
    ("serie a",            ["Serie A"]),
    ("liga italiana",      ["Serie A"]),
    ("italiana",           ["Serie A"]),
    ("italia",             ["Serie A"]),
    ("calcio",             ["Serie A"]),

    # ── Alemania ─────────────────────────────────────────────────
    ("bundesliga",         ["Bundesliga"]),
    ("liga alemana",       ["Bundesliga"]),
    ("alemana",            ["Bundesliga"]),
    ("alemania",           ["Bundesliga"]),

    # ── Francia ──────────────────────────────────────────────────
    ("ligue 1",            ["Ligue 1"]),
    ("ligue1",             ["Ligue 1"]),
    ("ligue 2",            ["Ligue 2"]),
    ("ligue2",             ["Ligue 2"]),
    ("segunda francesa",   ["Ligue 2"]),
    ("liga francesa",      ["Ligue 1", "Ligue 2"]),
    ("francesa",           ["Ligue 1", "Ligue 2"]),
    ("francia",            ["Ligue 1", "Ligue 2"]),

    # ── Champions / Copas ────────────────────────────────────────
    ("champions league",   ["Champions League"]),
    ("champions",          ["Champions League"]),
    ("uefa champions",     ["Champions League"]),
    ("ucl",                ["Champions League"]),
    ("copa libertadores",  ["Copa Libertadores"]),
    ("libertadores",       ["Copa Libertadores"]),
    ("copa sudamericana",  ["Copa Sudamericana"]),
    ("sudamericana",       ["Copa Sudamericana"]),

    # ── Arabia ───────────────────────────────────────────────────
    ("saudi pro league",   ["Saudi Pro League"]),
    ("saudi pro",          ["Saudi Pro League"]),
    ("saudi",              ["Saudi Pro League"]),
    ("liga saudita",       ["Saudi Pro League"]),
    ("liga arabe",         ["Saudi Pro League"]),
    ("liga árabe",         ["Saudi Pro League"]),
    ("saudita",            ["Saudi Pro League"]),
    ("arabe",              ["Saudi Pro League"]),
    ("árabe",              ["Saudi Pro League"]),
    ("arabia",             ["Saudi Pro League"]),

    # ── Argentina ────────────────────────────────────────────────
    ("liga profesional argentina", ["Liga Profesional Argentina"]),
    ("liga profesional",   ["Liga Profesional Argentina"]),
    ("primera argentina",  ["Liga Profesional Argentina"]),
    ("primera división argentina", ["Liga Profesional Argentina"]),
    ("primera division argentina", ["Liga Profesional Argentina"]),
    ("liga argentina",     ["Liga Profesional Argentina"]),
    ("argentina",          ["Liga Profesional Argentina"]),
    ("argentino",          ["Liga Profesional Argentina"]),
    ("afa",                ["Liga Profesional Argentina"]),

    # ── Perú ─────────────────────────────────────────────────────
    ("liga 1 peru",        ["Liga 1 Perú"]),
    ("liga 1 perú",        ["Liga 1 Perú"]),
    ("liga peruana",       ["Liga 1 Perú"]),
    ("peruana",            ["Liga 1 Perú"]),
    ("perú",               ["Liga 1 Perú"]),
    ("peru",               ["Liga 1 Perú"]),
    # "liga 1" debe ir al FINAL para no robar matches a "premier league",
    # "ligue 1", "1. deild", "liga 1 perú", etc.
    ("liga 1",             ["Liga 1 Perú"]),
]


def normalizar_liga(nombre: str | None) -> list[str]:
    """Convierte un nombre coloquial/alias de liga al/los nombre(s)
    oficial(es) que aparece(n) en LIGAS y en los fixtures.

    Retorna lista vacía si no se reconoce. Si el nombre ya ES oficial,
    devuelve [nombre] (busca match exacto contra las claves de LIGAS o
    LIGAS_CONFIG primero)."""
    if not nombre:
        return []
    n = nombre.strip().lower()

    # 1) Match exacto contra ligas oficiales (case-insensitive)
    for nombre_oficial in LIGAS.keys():
        if n == nombre_oficial.lower():
            return [nombre_oficial]
    # 2) Match parcial contra nombres oficiales (ej: "saudi pro" en
    #    "saudi pro league" → matchea). Útil cuando el LLM emite el
    #    nombre casi-oficial.
    for nombre_oficial in LIGAS.keys():
        no = nombre_oficial.lower()
        if n in no or no in n:
            # Pero solo si NO matchea un alias más específico abajo.
            # Lo dejamos como segundo paso, ver más abajo.
            pass
    # 3) Match por alias (ordenado por especificidad)
    for alias, ligas_oficiales in _LIGA_ALIASES:
        if alias in n:
            # Filtrar las que efectivamente están cargadas en LIGAS
            disponibles = [l for l in ligas_oficiales if l in LIGAS] or ligas_oficiales
            return disponibles
    # 4) Fallback: match parcial contra ligas oficiales
    for nombre_oficial in LIGAS.keys():
        no = nombre_oficial.lower()
        if n in no or no in n:
            return [nombre_oficial]
    return []


def buscar_liga_info(nombre: str | None) -> tuple[str | None, dict | None]:
    """Resuelve un alias/nombre de liga al primer match en LIGAS y
    devuelve (nombre_oficial, datos_dict). Si no se encuentra, devuelve
    (None, None). Es la versión "una sola liga" usada por funciones que
    necesitan apuntar a UN torneo concreto (combinada AUTO, análisis
    completo, etc.). Multi-liga lo manejan los callers con normalizar_liga."""
    matches = normalizar_liga(nombre)
    for nombre_oficial in matches:
        if nombre_oficial in LIGAS:
            return nombre_oficial, LIGAS[nombre_oficial]
    return None, None


def detectar_liga_en_mensaje(msg: str) -> list[str]:
    """Busca TODOS los aliases de liga en el mensaje y devuelve la
    union de sus ligas. Preserva orden de especificidad: alias primero
    (en orden), luego nombres oficiales.

    #0j: Soporta multi-liga. Ej: "Premier e Italia" → ["Premier League", "Serie A"]
    """
    if not msg:
        return []
    m = msg.lower()
    resultado = []
    vistos = set()  # Evitar duplicados (ej: si "la liga" matchea tanto alias como nombre)

    # 1) Aliases (orden de especificidad)
    for alias, ligas_oficiales in _LIGA_ALIASES:
        if alias in m:
            disponibles = [l for l in ligas_oficiales if l in LIGAS] or ligas_oficiales
            for liga in disponibles:
                if liga not in vistos:
                    resultado.append(liga)
                    vistos.add(liga)

    # 2) Nombres oficiales (case-insensitive) — si no fueron encontrados por alias
    for nombre_oficial in LIGAS.keys():
        if nombre_oficial.lower() in m and nombre_oficial not in vistos:
            resultado.append(nombre_oficial)
            vistos.add(nombre_oficial)

    return resultado


# ── #0k: detección de estado de liga (receso, sin carga, próximo partido) ──
# Cache con TTL para no hacer requests repetidos a SofaScore.
import time as _time_mod
_LIGA_ESTADO_CACHE: dict[str, tuple[float, dict]] = {}
_LIGA_ESTADO_TTL = 3600  # 1 hora


def estado_liga(nombre_oficial: str) -> dict:
    """Retorna el estado actual de una liga:
       {
         'cargada': bool,                 # ¿está en LIGAS (cargada al startup)?
         'tiene_partidos_proximos': bool, # ¿hay al menos 1 partido futuro?
         'proximo_partido': dict | None,  # {'home', 'away', 'fecha_str', 'dias'}
         'mensaje': str                   # mensaje listo para mostrar al user
       }
    Si la liga no está cargada → cargada=False, mensaje genérico.
    Si está cargada pero sin próximos → en receso, intenta dar próximo.
    Cache de 1h. Pensado para mostrar info útil cuando una combinada/listado
    de una liga no devuelve resultados.
    """
    if not nombre_oficial:
        return {'cargada': False, 'tiene_partidos_proximos': False,
                'proximo_partido': None,
                'mensaje': "Liga no reconocida."}

    # Cache hit
    cached = _LIGA_ESTADO_CACHE.get(nombre_oficial)
    if cached and (_time_mod.time() - cached[0]) < _LIGA_ESTADO_TTL:
        return cached[1]

    # 1) Liga no cargada en LIGAS → no es soportada en este momento
    if nombre_oficial not in LIGAS:
        info = {
            'cargada': False,
            'tiene_partidos_proximos': False,
            'proximo_partido': None,
            'mensaje': f"{nombre_oficial} no está disponible actualmente (no se cargó al iniciar el servidor).",
        }
        _LIGA_ESTADO_CACHE[nombre_oficial] = (_time_mod.time(), info)
        return info

    # 2) Liga cargada → consultar próximos eventos a SofaScore
    datos = LIGAS[nombre_oficial]
    liga_id = datos['id']; temporada_id = datos['temporada']

    proximo = None
    try:
        sesion = _nueva_sesion()
        url = (f"https://www.sofascore.com/api/v1/unique-tournament/"
               f"{liga_id}/season/{temporada_id}/events/next/0")
        resp = sesion.get(url, timeout=10)
        if resp.status_code == 200:
            eventos = resp.json().get('events', [])
            ahora_ts = _time_mod.time()
            futuros = [e for e in eventos
                       if e.get('startTimestamp', 0) > ahora_ts]
            if futuros:
                futuros.sort(key=lambda e: e.get('startTimestamp', 0))
                e = futuros[0]
                ts = e['startTimestamp']
                from datetime import datetime as _dt, timedelta as _td
                tz_h = get_tz_offset_hours() if 'get_tz_offset_hours' in globals() else _SERVER_TZ_AT_LOAD
                fecha_local = _dt.utcfromtimestamp(ts) + _td(hours=tz_h)
                dias = (fecha_local.date() - (_dt.utcnow() + _td(hours=tz_h)).date()).days
                proximo = {
                    'home': e.get('homeTeam', {}).get('name', '?'),
                    'away': e.get('awayTeam', {}).get('name', '?'),
                    'fecha_str': fecha_local.strftime('%d/%m/%Y %H:%M'),
                    'dias': dias,
                }
    except Exception:
        pass  # silencioso, fallback a "en receso"

    if proximo:
        if proximo['dias'] == 0:
            cuando = f"hoy a las {proximo['fecha_str'].split(' ')[1]}"
            cabecera = f"{nombre_oficial} tiene partido hoy"
        elif proximo['dias'] == 1:
            cuando = f"mañana ({proximo['fecha_str']})"
            cabecera = f"{nombre_oficial} no tiene partidos hoy"
        elif proximo['dias'] <= 7:
            cuando = f"en {proximo['dias']} días ({proximo['fecha_str']})"
            cabecera = f"{nombre_oficial} no tiene partidos hoy"
        else:
            cuando = f"el {proximo['fecha_str']} (en {proximo['dias']} días)"
            cabecera = f"{nombre_oficial} está en pausa"
        info = {
            'cargada': True,
            'tiene_partidos_proximos': True,
            'proximo_partido': proximo,
            'mensaje': (f"{cabecera}. Próximo: {proximo['home']} vs {proximo['away']} {cuando}."),
        }
    else:
        info = {
            'cargada': True,
            'tiene_partidos_proximos': False,
            'proximo_partido': None,
            'mensaje': (f"{nombre_oficial} está en receso (fin de temporada "
                        f"o pausa estacional). No hay partidos próximos confirmados."),
        }

    _LIGA_ESTADO_CACHE[nombre_oficial] = (_time_mod.time(), info)
    return info


# Full system prompt (BASE + fixtures) — built in initialize_engine()
SYSTEM_PROMPT: str = ""

MAX_DIAS_HISTORIAL = 60

# ── SofaScore helpers ────────────────────────────────────────────────

def _nueva_sesion():
    session = cf_requests.Session(impersonate="chrome124")
    proxy_url = os.getenv("PROXY_URL", "")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session

def fetch_api(sesion, url):
    return sesion.get(url, timeout=15).json()

# ── Stats config ────────────────────────────────────────────────────

_STATS_A_PRECOMPUTAR = [
    ("ALL", "Corner kicks"),
    ("1ST", "Corner kicks"),
    ("2ND", "Corner kicks"),
    ("ALL", "Yellow cards"),
    ("1ST", "Yellow cards"),
    ("2ND", "Yellow cards"),
    ("ALL", "Red cards"),
    ("1ST", "Red cards"),
    ("2ND", "Red cards"),
    ("ALL", "Shots on target"),
    ("1ST", "Shots on target"),
    ("2ND", "Shots on target"),
    ("ALL", "Fouls"),
    ("1ST", "Fouls"),
    ("2ND", "Fouls"),
    ("ALL", "Total shots"),
]

# ── SofaScore scraping ───────────────────────────────────────────────

def _buscar_team_id(sesion, nombre_equipo: str) -> int | None:
    """
    #0p: Busca el team_id en SofaScore por nombre. Usado para fallback
    cuando la búsqueda por rondas no devuelve nada (ligas pequeñas,
    receso, estructura de temporada distinta).
    """
    try:
        data = fetch_api(
            sesion,
            f"https://www.sofascore.com/api/v1/search/teams/{nombre_equipo}"
        )
        teams = data.get("teams") or []
        nl = nombre_equipo.lower()
        # Primero match exacto (case-insensitive)
        for t in teams:
            if t.get("name", "").lower() == nl:
                return t.get("id")
        # Luego match parcial
        for t in teams:
            if nl in t.get("name", "").lower():
                return t.get("id")
    except Exception as e:
        print(f"[#0p] _buscar_team_id falló para '{nombre_equipo}': {e}")
    return None


def _partidos_via_endpoint_equipo(sesion, nombre_equipo: str, liga_id: int,
                                   cutoff: float, ultimas_rondas: int = 5) -> list:
    """
    #0p: Fallback que usa /team/{id}/events para obtener partidos directos
    del equipo. Útil cuando la búsqueda por rondas falla (Islandia, etc.).
    Filtra por liga_id para mantener consistencia con el contexto.
    """
    team_id = _buscar_team_id(sesion, nombre_equipo)
    if not team_id:
        return []
    try:
        data = fetch_api(
            sesion,
            f"https://www.sofascore.com/api/v1/team/{team_id}/events/last/0"
        )
    except Exception as e:
        print(f"[#0p] /team/{team_id}/events falló: {e}")
        return []

    partidos = []
    for evento in data.get("events", []):
        if evento.get("status", {}).get("type", "") != "finished":
            continue
        start = evento.get("startTimestamp", 0)
        if start < cutoff:
            continue
        # Filtrar por liga (uniqueTournament.id == liga_id)
        ut = (evento.get("tournament", {}) or {}).get("uniqueTournament", {}) or {}
        if ut.get("id") != liga_id:
            continue
        partidos.append(evento)
    partidos.sort(key=lambda e: e.get("startTimestamp", 0), reverse=True)
    return partidos[:ultimas_rondas]


# Flag por-equipo: True si los partidos fueron obtenidos con MAX_DIAS expandido
# (>= 180 días). Usado para avisar al usuario que está analizando datos viejos.
_ULTIMOS_DATOS_VIEJOS: dict[str, bool] = {}


def obtener_partidos_equipo(sesion, nombre_equipo, liga_id, temporada_id, rondas_totales, ultimas_rondas=5):
    # ── Cache check ──────────────────────────────────────────────────
    cached = cache_manager.get_partidos_equipo(nombre_equipo, liga_id, temporada_id)
    if cached is not None:
        print(f"[cache] partidos {nombre_equipo} → hit")
        return cached[:ultimas_rondas]

    # #0p: usar UTC (consistente con resto del sistema, no depende del OS).
    ahora = datetime.utcnow().timestamp()

    def _buscar_con_cutoff(dias: int) -> list:
        cutoff = ahora - dias * 86400
        partidos: list = []
        # Estrategia 1: búsqueda por rondas (rápida en ligas grandes).
        for ronda in range(rondas_totales + 1, max(0, rondas_totales - ultimas_rondas - 6), -1):
            try:
                data = fetch_api(
                    sesion,
                    f"https://www.sofascore.com/api/v1/unique-tournament/"
                    f"{liga_id}/season/{temporada_id}/events/round/{ronda}"
                )
            except Exception:
                continue
            for evento in data.get("events", []):
                status = evento.get("status", {}).get("type", "")
                start  = evento.get("startTimestamp", 0)
                if status != "finished" or start < cutoff:
                    continue
                home = evento.get("homeTeam", {}).get("name", "")
                away = evento.get("awayTeam", {}).get("name", "")
                if not home or not away:
                    continue
                if nombre_equipo.lower() in home.lower() or nombre_equipo.lower() in away.lower():
                    partidos.append(evento)
            if len(partidos) >= ultimas_rondas:
                break
        # Estrategia 2 (fallback): endpoint /team/{id}/events si rondas falló.
        if not partidos:
            print(f"[#0p] rondas vacías para '{nombre_equipo}' (cutoff={dias}d) → fallback /team/events")
            partidos = _partidos_via_endpoint_equipo(
                sesion, nombre_equipo, liga_id, cutoff, ultimas_rondas
            )
        return partidos

    partidos = _buscar_con_cutoff(MAX_DIAS_HISTORIAL)
    datos_viejos = False

    # #0p: Si no hay nada con ventana default, reintentar con 180 días
    # (cubre receso de temporada, semestre anterior). Avisar al user.
    if not partidos:
        print(f"[#0p] sin partidos en {MAX_DIAS_HISTORIAL}d → reintentar con 180d para '{nombre_equipo}'")
        partidos = _buscar_con_cutoff(180)
        if partidos:
            datos_viejos = True
            print(f"[#0p] encontrados {len(partidos)} partidos viejos (>{MAX_DIAS_HISTORIAL}d) para '{nombre_equipo}'")

    partidos.sort(key=lambda e: e.get("startTimestamp", 0), reverse=True)
    result = partidos[:ultimas_rondas]

    # Registrar si los datos son viejos (consumido por precomputar_stats_equipo
    # para insertar aviso en el contexto del LLM).
    _ULTIMOS_DATOS_VIEJOS[nombre_equipo.lower()] = datos_viejos

    if result:
        cache_manager.set_partidos_equipo(nombre_equipo, liga_id, temporada_id, result)
    return result


def obtener_estadisticas(sesion, evento_id):
    # ── Cache check ──────────────────────────────────────────────────
    cached = cache_manager.get_stats_partido(evento_id)
    if cached is not None:
        print(f"[cache] stats evento {evento_id} → hit")
        return cached

    try:
        data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/event/{evento_id}/statistics")
        stats = {}
        for grupo in data.get("statistics", []):
            periodo = grupo["period"]
            for g in grupo["groups"]:
                for item in g["statisticsItems"]:
                    clave = f"{periodo}_{item['name']}"
                    if clave not in stats:
                        stats[clave] = {"home": item.get("home", "?"), "away": item.get("away", "?")}
        if stats:
            cache_manager.set_stats_partido(evento_id, stats)
        return stats
    except Exception:
        return {}


# #0l: Desviación estándar ESTIMADA por tipo de stat. Refleja la variabilidad
# real observada en datos de fútbol (estimaciones — validar con datos cuando
# sea posible). Se usa para calibrar líneas y confianza de forma consistente
# con el riesgo real en la práctica, no solo con el promedio.
_SIGMA_POR_STAT: dict[str, float] = {
    "corners":             2.5,
    "goles":               1.0,   # menor varianza que corners; ajustado tras testeo
    "tarjetas_amarillas":  1.8,
    "tarjetas_rojas":      0.5,
    "remates":             2.0,
    "faltas":              3.0,
    # Stats _1h / _2h tienden a tener menos varianza (la mitad del partido)
    "corners_1h":          1.5,
    "corners_2h":          1.8,
    "goles_1h":            0.7,
    "goles_2h":            0.8,
}


def _sigma_para(stat_key: str | None) -> float:
    """Devuelve σ esperada para el stat dado. Default 1.0 (genérico)."""
    if not stat_key:
        return 1.0
    if stat_key in _SIGMA_POR_STAT:
        return _SIGMA_POR_STAT[stat_key]
    # Fallback: matchear por prefijo (ej: "corners_xyz" → corners)
    base = stat_key.split("_")[0]
    return _SIGMA_POR_STAT.get(base, 1.0)


# ── Temporal decay + rival quality ──────────────────────────────────────────
_LAMBDA_DECAY = 0.02        # e^(-λ*días): 30d→55%, 60d→30%, 90d→17%
_RIVAL_CACHE_TTL = 3600     # 1 hora
_CACHE_FUERZA_RIVAL: dict[str, tuple[float, dict]] = {}
_CACHE_TEAM_ID:     dict[str, int | None] = {}  # nombre_lower → team_id SofaScore


def _weighted_avg(pairs: list[tuple[float, float]]) -> float | None:
    """Promedio ponderado. pairs = [(valor, peso), ...]. Retorna None si vacío."""
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs)
    if total_w == 0:
        return None
    return sum(v * w for v, w in pairs) / total_w


def _calcular_fuerza_rival_ligera(sesion, nombre_rival: str, liga_id: int,
                                   temporada_id: int, rondas_totales: int,
                                   n: int = 5) -> dict:
    """
    Retorna {attack, defense} del rival usando su endpoint de equipo directo
    (/team/{id}/events/last/0) — 2 requests en vez de 10+ ronda-por-ronda.
    Resultado cacheado 1h. Fallback a obtener_partidos_equipo si team_id no se encuentra.
    """
    cache_key = f"{nombre_rival.lower()}_{liga_id}"
    cached = _CACHE_FUERZA_RIVAL.get(cache_key)
    if cached and (time.time() - cached[0]) < _RIVAL_CACHE_TTL:
        return cached[1]

    # Estrategia rápida: endpoint directo de equipo (cualquier competencia)
    partidos: list = []
    nombre_lower = nombre_rival.lower()
    if nombre_lower not in _CACHE_TEAM_ID:
        _CACHE_TEAM_ID[nombre_lower] = _buscar_team_id(sesion, nombre_rival)
    team_id = _CACHE_TEAM_ID[nombre_lower]

    if team_id:
        try:
            data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/team/{team_id}/events/last/0")
            for ev in data.get("events", []):
                if ev.get("status", {}).get("type") == "finished":
                    partidos.append(ev)
                    if len(partidos) >= n:
                        break
        except Exception:
            partidos = []

    # Fallback: búsqueda ronda-por-ronda si el endpoint no devolvió nada
    if not partidos:
        partidos = obtener_partidos_equipo(
            sesion, nombre_rival, liga_id, temporada_id, rondas_totales, n
        )

    scored: list[float] = []
    conceded: list[float] = []
    for e in partidos:
        home_name = e.get("homeTeam", {}).get("name", "")
        es_local = nombre_rival.lower() in home_name.lower()
        gh = e.get("homeScore", {}).get("current")
        ga = e.get("awayScore", {}).get("current")
        if gh is not None and ga is not None:
            scored.append(float(gh if es_local else ga))
            conceded.append(float(ga if es_local else gh))

    _DEFAULT = 1.2
    result = {
        "attack":  sum(scored)   / len(scored)   if scored   else _DEFAULT,
        "defense": sum(conceded) / len(conceded) if conceded else _DEFAULT,
    }
    _CACHE_FUERZA_RIVAL[cache_key] = (time.time(), result)
    return result


def calcular_lineas_y_confianza(total_esperado: float,
                                  margen_minimo: float = 1.0,
                                  stat_key: str | None = None) -> tuple:
    """
    Retorna (línea_directa, línea_segura, confianza, línea_conservadora).

    #0l: La calibración de confianza usa σ (desviación estándar esperada)
    POR TIPO DE STAT. Picks "Alta confianza" requieren margen ≥ 1σ
    respecto al total esperado, NO un margen fijo. Esto refleja la
    variabilidad real:
      - corners (σ=2.5): para μ=13, Alta = Over 10.5 (margen 2.5 = 1σ)
      - goles   (σ=1.5): para μ=2.8, Alta = Over 1.5 (margen 1.3 ≈ 1σ)
      - faltas  (σ=3.0): para μ=22,  Alta = Over 18.5 (margen 3.5 > 1σ)

    Niveles (todos como múltiplos de σ desde μ):
      Muy alta 🟢: margen ≥ 1.5σ
      Alta 🟢:    margen ≥ 1.0σ
      Media 🟡:   margen ≥ 0.5σ
      Baja 🔴:    margen < 0.5σ

    Parámetros:
      stat_key: clave del stat (corners, goles, …). Si None, usa σ=1.0
                (comportamiento legacy, equivalente al fix anterior).
    """
    sigma = _sigma_para(stat_key)

    # Thresholds en unidades absolutas (calibrados por σ del stat).
    margen_alta     = max(margen_minimo, sigma * 1.0)   # ≥ 1σ
    margen_muy_alta = sigma * 1.5                       # ≥ 1.5σ
    margen_media    = max(0.5, sigma * 0.5)             # ≥ 0.5σ

    base = int(total_esperado)
    linea_directa = base + 0.5
    if linea_directa >= total_esperado:
        linea_directa -= 1.0

    # Línea segura: primer X.5 que alcanza el margen de "Alta" (≥ 1σ).
    linea_segura = linea_directa
    while total_esperado - linea_segura < margen_alta:
        if linea_segura <= 0.5:
            break
        linea_segura -= 1.0
        if linea_segura < 0.5:
            linea_segura = 0.5

    margen = total_esperado - linea_segura

    if margen >= margen_muy_alta:
        confianza = "Muy alta 🟢"
    elif margen >= margen_alta:
        confianza = "Alta 🟢"
    elif margen >= margen_media:
        confianza = "Media 🟡"
    else:
        confianza = "Baja 🔴"

    # Línea conservadora: primer X.5 que alcanza Muy Alta (≥ 1.5σ).
    linea_conservadora = linea_segura
    while total_esperado - linea_conservadora < margen_muy_alta:
        if linea_conservadora <= 0.5:
            break
        linea_conservadora -= 1.0
        if linea_conservadora < 0.5:
            linea_conservadora = 0.5

    return (
        f"Over {linea_directa:.1f}",
        f"Over {linea_segura:.1f}",
        confianza,
        f"Over {linea_conservadora:.1f}",
    )


# ── Poisson helpers (1X2) ────────────────────────────────────────────

def _poisson_prob(lam: float, k: int) -> float:
    """Probabilidad de k eventos con distribución Poisson de media lam."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


# Orden de niveles de confianza (menor índice = mayor confianza)
_NIVELES_CONFIANZA = ["Muy alta 🟢", "Alta 🟢", "Media 🟡", "Baja 🔴"]


def _ajustar_confianza_por_track_record(
    foco: str, liga: str | None, rango: str | None,
    confianza_actual: str, track_record: dict | None
) -> tuple[str, dict | None]:
    """
    Ajusta la confianza según el track record colectivo.
    tasa < 0.45 → bajar un nivel | tasa > 0.70 → subir un nivel | 0.45-0.70 → sin cambio
    """
    if not track_record:
        return confianza_actual, None
    tasa = track_record["tasa"]
    try:
        idx = _NIVELES_CONFIANZA.index(confianza_actual)
    except ValueError:
        return confianza_actual, track_record
    if tasa < 0.45:
        idx = min(idx + 1, len(_NIVELES_CONFIANZA) - 1)
    elif tasa > 0.70:
        idx = max(idx - 1, 0)
    return _NIVELES_CONFIANZA[idx], track_record


def calcular_1x2(xg1: float, xg2: float, max_goles: int = 8) -> tuple:
    """
    Calcula (p_local, p_empate, p_visitante) usando modelo Poisson.
    xg1 = expected goals equipo local, xg2 = expected goals equipo visitante.
    """
    p_local = p_empate = p_visitante = 0.0
    for h in range(max_goles + 1):
        ph = _poisson_prob(xg1, h)
        for a in range(max_goles + 1):
            pa = _poisson_prob(xg2, a)
            p = ph * pa
            if h > a:
                p_local += p
            elif h == a:
                p_empate += p
            else:
                p_visitante += p
    return p_local, p_empate, p_visitante


def precomputar_stats_equipo(sesion, nombre_equipo, liga_id, temporada_id, rondas_totales, n=10):
    partidos = obtener_partidos_equipo(sesion, nombre_equipo, liga_id, temporada_id, rondas_totales, n)

    if not partidos:
        return f"ESTADÍSTICAS DE {nombre_equipo.upper()} (sin datos disponibles)", {}

    ahora  = datetime.utcnow().timestamp()
    _tz_h  = get_tz_offset_hours()

    # ── Fase 1: fuerza de rivales (solo scores, sin stats) ───────────────────
    rival_data: list[tuple] = []
    for e in partidos:
        home_name = e.get("homeTeam", {}).get("name", "")
        away_name = e.get("awayTeam", {}).get("name", "")
        es_local  = nombre_equipo.lower() in home_name.lower()
        rival     = away_name if es_local else home_name
        rf = _calcular_fuerza_rival_ligera(
            sesion, rival, liga_id, temporada_id, rondas_totales, n=3
        )
        rival_data.append((e, es_local, rival, rf))

    # ── Fase 2: promedios de liga como referencia para normalizar ────────────
    _DEFAULT_AVG   = 1.2
    rival_attacks  = [rf["attack"]  for _, _, _, rf in rival_data]
    rival_defenses = [rf["defense"] for _, _, _, rf in rival_data]
    league_avg_attack  = sum(rival_attacks)  / len(rival_attacks)  if rival_attacks  else _DEFAULT_AVG
    league_avg_defense = sum(rival_defenses) / len(rival_defenses) if rival_defenses else _DEFAULT_AVG

    # ── Fase 3: acumular stats ponderadas ────────────────────────────────────
    # Cada acumulador guarda (valor, peso) → _weighted_avg al final
    acum         = {f"{p}_{s}": [] for p, s in _STATS_A_PRECOMPUTAR}
    acum_against = {f"{p}_{s}": [] for p, s in _STATS_A_PRECOMPUTAR}
    goles = []; goles_against = []
    goles_1h = []; goles_against_1h = []
    goles_2h = []; goles_against_2h = []
    refs = []

    for e, es_local, rival, rf in rival_data:
        home_name = e.get("homeTeam", {}).get("name", "")
        away_name = e.get("awayTeam", {}).get("name", "")
        ronda     = e.get("roundInfo", {}).get("round", "?")

        # Decaimiento temporal: e^(-λ * días_atrás)
        ts       = e.get("startTimestamp", ahora)
        days_ago = max(0.0, (ahora - ts) / 86400.0)
        t_w      = math.exp(-_LAMBDA_DECAY * days_ago)

        # Factor de rival (clippeado a [0.3, 3.0])
        # Para stats generadas: rival que defiende bien → más mérito → peso mayor
        rival_hardness = max(0.3, min(3.0, league_avg_defense / max(rf["defense"], 0.1)))
        # Para stats concedidas: rival que ataca bien → muestra más representativa
        rival_pressure = max(0.3, min(3.0, rf["attack"] / max(league_avg_attack, 0.1)))

        w_attack  = t_w * rival_hardness
        w_defense = t_w * rival_pressure

        gh = e.get("homeScore", {}).get("current")
        ga = e.get("awayScore", {}).get("current")
        if gh is not None and ga is not None:
            goles.append((float(gh if es_local else ga), w_attack))
            goles_against.append((float(ga if es_local else gh), w_defense))

        gh1 = e.get("homeScore", {}).get("period1")
        ga1 = e.get("awayScore", {}).get("period1")
        gh2 = e.get("homeScore", {}).get("period2")
        ga2 = e.get("awayScore", {}).get("period2")
        if gh1 is not None and ga1 is not None:
            goles_1h.append((float(gh1 if es_local else ga1), w_attack))
            goles_against_1h.append((float(ga1 if es_local else gh1), w_defense))
        if gh2 is not None and ga2 is not None:
            goles_2h.append((float(gh2 if es_local else ga2), w_attack))
            goles_against_2h.append((float(ga2 if es_local else gh2), w_defense))

        fecha_str = (
            (datetime.utcfromtimestamp(ts) + timedelta(hours=_tz_h)).strftime("%d/%m/%Y")
            if e.get("startTimestamp") else "?"
        )
        refs.append(
            f"{fecha_str} R{ronda}: {home_name} {gh}-{ga} {away_name} "
            f"({'local' if es_local else 'visitante'}, "
            f"rival atk={rf['attack']:.1f}/def={rf['defense']:.1f}, w={w_attack:.2f})"
        )

        stats = obtener_estadisticas(sesion, e["id"])
        time.sleep(0.3)

        for periodo, stat_name in _STATS_A_PRECOMPUTAR:
            clave = f"{periodo}_{stat_name}"
            if clave not in stats:
                continue
            col_p = "home" if es_local else "away"
            col_r = "away" if es_local else "home"
            try:
                acum[clave].append((int(str(stats[clave][col_p])), w_attack))
            except (ValueError, TypeError):
                pass
            try:
                acum_against[clave].append((int(str(stats[clave][col_r])), w_defense))
            except (ValueError, TypeError):
                pass

    # ── Fase 4: contexto y promedios ponderados ──────────────────────────────
    n_partidos = len(rival_data)
    lineas = [
        f"ESTADÍSTICAS DE {nombre_equipo.upper()} "
        f"(últimos {n_partidos} partidos, ponderados por recencia y calidad de rival):"
    ]
    if _ULTIMOS_DATOS_VIEJOS.get(nombre_equipo.lower(), False):
        lineas.append(
            f"  ⚠️ AVISO: {nombre_equipo} no tiene partidos recientes (últimos {MAX_DIAS_HISTORIAL} días); "
            f"se están usando datos de hasta 180 días atrás. La plantilla y forma pueden haber cambiado."
        )
    lineas.append(f"  Partidos: {' | '.join(refs)}")

    def _linea(nombre, pairs):
        avg = _weighted_avg(pairs)
        if avg is not None:
            lineas.append(f"  {nombre}: {[v for v,_ in pairs]} → promedio ponderado = {avg:.2f}")

    _linea("Goles anotados",     goles)
    _linea("Goles recibidos",    goles_against)
    _linea("Goles anotados 1T",  goles_1h)
    _linea("Goles recibidos 1T", goles_against_1h)
    _linea("Goles anotados 2T",  goles_2h)
    _linea("Goles recibidos 2T", goles_against_2h)

    for periodo, stat_name in _STATS_A_PRECOMPUTAR:
        clave = f"{periodo}_{stat_name}"
        avg = _weighted_avg(acum[clave])
        if avg is not None:
            lineas.append(f"  {clave}: {[v for v,_ in acum[clave]]} → promedio ponderado = {avg:.2f}")
        avg_a = _weighted_avg(acum_against[clave])
        if avg_a is not None:
            lineas.append(f"  {clave} (concedidos): {[v for v,_ in acum_against[clave]]} → promedio ponderado = {avg_a:.2f}")

    # ── Promedios dict ────────────────────────────────────────────────────────
    promedios: dict = {}

    def _set(k, pairs):
        v = _weighted_avg(pairs)
        if v is not None:
            promedios[k] = v

    _set("goles",            goles)
    _set("goles_against",    goles_against)
    _set("goles_1h",         goles_1h)
    _set("goles_against_1h", goles_against_1h)
    _set("goles_2h",         goles_2h)
    _set("goles_against_2h", goles_against_2h)

    # btts_score: proporción ponderada de partidos donde anotó ≥1 gol
    if goles:
        total_w = sum(w for _, w in goles)
        if total_w > 0:
            promedios["btts_score"] = sum(w for v, w in goles if v > 0) / total_w

    for periodo, stat_name in _STATS_A_PRECOMPUTAR:
        clave = f"{periodo}_{stat_name}"
        _set(clave,              acum[clave])
        _set(f"{clave}_against", acum_against[clave])

    # ── attack_force / defense_force normalizados ─────────────────────────────
    # Clipeados a [0.5, 2.5]: evita que muestras pequeñas generen extremos
    # irreales (ej: Boca 0.47 defense_force → O'Higgins xG casi cero).
    _FORCE_MIN, _FORCE_MAX = 0.5, 2.5
    g_avg  = promedios.get("goles")
    ga_avg = promedios.get("goles_against")
    if g_avg is not None and league_avg_attack > 0:
        promedios["attack_force"]  = max(_FORCE_MIN, min(_FORCE_MAX, g_avg  / league_avg_attack))
    if ga_avg is not None and league_avg_defense > 0:
        promedios["defense_force"] = max(_FORCE_MIN, min(_FORCE_MAX, ga_avg / league_avg_defense))
    promedios["league_avg_goals"] = (league_avg_attack + league_avg_defense) / 2
    promedios["n_partidos"] = n_partidos

    af = promedios.get("attack_force")
    df = promedios.get("defense_force")
    if af is not None and df is not None:
        lineas.append(
            f"  Fuerza de ataque = {af:.2f} (>1 = mejor que promedio de rivales) | "
            f"Fuerza defensiva = {df:.2f} (<1 = mejor que promedio de rivales)"
        )

    return "\n".join(lineas), promedios


_MIN_PARTIDOS_ANALISIS = 8  # umbral mínimo antes de complementar con otra liga


def _merge_promedios(p1: dict, p2: dict, n1: int, n2: int) -> dict:
    """Combina dos dicts de promedios ponderando por cantidad de partidos.
    attack_force/defense_force se recalculan del merged para que sean coherentes."""
    total = n1 + n2
    if total == 0:
        return p1
    w1 = n1 / total
    w2 = n2 / total
    skip = {"n_partidos", "attack_force", "defense_force", "league_avg_goals"}
    merged: dict = {}
    for k in set(p1) | set(p2):
        if k in skip:
            continue
        if k in p1 and k in p2:
            merged[k] = p1[k] * w1 + p2[k] * w2
        elif k in p1:
            merged[k] = p1[k]
        else:
            merged[k] = p2[k]
    merged["n_partidos"] = total
    lg = p1.get("league_avg_goals", 1.2) * w1 + p2.get("league_avg_goals", 1.2) * w2
    merged["league_avg_goals"] = lg
    _FORCE_MIN, _FORCE_MAX = 0.5, 2.5
    if lg > 0:
        if "goles" in merged:
            merged["attack_force"] = max(_FORCE_MIN, min(_FORCE_MAX, merged["goles"] / lg))
        if "goles_against" in merged:
            merged["defense_force"] = max(_FORCE_MIN, min(_FORCE_MAX, merged["goles_against"] / lg))
    return merged


def hacer_analisis_completo(equipo1: str, equipo2: str, liga_nombre: str, progress_cb=None):
    """
    Returns (contexto_str, evento_id_proximo, info_ronda, liga_info_dict).
    liga_info_dict = {id, temporada, rondas} — the actual values used.
    """
    _liga_nombre_oficial, liga = buscar_liga_info(liga_nombre)
    if not liga:
        # Liga no encontrada → devolver vacío en vez de usar una liga incorrecta
        ctx_vacio = (
            "DATOS REALES DE SOFASCORE (promedios ya calculados por equipo):\n\n"
            f"[{equipo1}]: sin datos disponibles\n\n"
            f"[{equipo2}]: sin datos disponibles\n\n"
            "LÍNEAS DE APUESTA PRE-CALCULADAS POR PYTHON:\n"
            "  (sin datos suficientes)\n"
        )
        return ctx_vacio, None, "", {"id": -1, "temporada": -1, "rondas": 0}, {}, {}, {}

    # #0i: normalizar al nombre oficial para que el resto de la función
    # y el header del análisis muestren la liga correcta aunque el LLM
    # haya enviado un alias.
    if _liga_nombre_oficial:
        liga_nombre = _liga_nombre_oficial
    liga_id      = liga["id"]
    temporada_id = liga["temporada"]
    rondas       = liga["rondas"]

    sesion = _nueva_sesion()

    # Actualizar ronda real
    try:
        rd = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/"
                               f"{liga_id}/season/{temporada_id}/rounds")
        rl = rd.get("rounds", [])
        rondas = (rd.get("currentRound", {}).get("round")
                  or (rl[-1].get("round", rondas) if rl else rondas))
    except Exception:
        pass

    stats_eq1, prom1 = precomputar_stats_equipo(sesion, equipo1, liga_id, temporada_id, rondas)
    stats_eq2, prom2 = precomputar_stats_equipo(sesion, equipo2, liga_id, temporada_id, rondas)

    # Si algún equipo no tiene historial suficiente en la liga del fixture, complementar
    def _buscar_en_otras_ligas(nombre_eq, stats_orig, prom_orig):
        n_orig = prom_orig.get("n_partidos", 0) if prom_orig else 0
        if n_orig >= _MIN_PARTIDOS_ANALISIS:
            return stats_orig, prom_orig

        # Identificar en qué ligas juega el equipo via team endpoint
        # (2 requests) en vez de probar las 13 ligas ronda-por-ronda (130+ requests).
        nombre_lower = nombre_eq.lower()
        if nombre_lower not in _CACHE_TEAM_ID:
            _CACHE_TEAM_ID[nombre_lower] = _buscar_team_id(sesion, nombre_eq)
        team_id = _CACHE_TEAM_ID[nombre_lower]

        ligas_a_probar: list[tuple[str, dict]] = []
        if team_id:
            try:
                from collections import Counter
                data_t = fetch_api(sesion, f"https://www.sofascore.com/api/v1/team/{team_id}/events/last/0")
                ut_counter: Counter = Counter()
                for e in data_t.get("events", []):
                    if e.get("status", {}).get("type") == "finished":
                        ut_id = ((e.get("tournament") or {}).get("uniqueTournament") or {}).get("id")
                        if ut_id:
                            ut_counter[ut_id] += 1
                for ut_id, _ in ut_counter.most_common():
                    for nombre_alt, datos_alt in LIGAS.items():
                        if datos_alt["id"] == ut_id and nombre_alt != liga_nombre:
                            ligas_a_probar.append((nombre_alt, datos_alt))
                            break
            except Exception:
                pass

        # Fallback: si el team endpoint no identificó ligas conocidas, probar todas
        if not ligas_a_probar:
            ligas_a_probar = [(n, d) for n, d in LIGAS.items() if n != liga_nombre]

        modo = "complementando" if n_orig > 0 else "buscando"
        for nombre_alt, datos_alt in ligas_a_probar:
            if progress_cb:
                progress_cb(f"⚠️ Pocos datos de {nombre_eq} en {liga_nombre} ({n_orig}p) — {modo} en {nombre_alt}...")
            try:
                rd_alt = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/"
                                          f"{datos_alt['id']}/season/{datos_alt['temporada']}/rounds")
                rl_alt = rd_alt.get("rounds", [])
                rondas_alt = (rd_alt.get("currentRound", {}).get("round")
                              or (rl_alt[-1].get("round", datos_alt["rondas"]) if rl_alt else datos_alt["rondas"]))
            except Exception:
                rondas_alt = datos_alt["rondas"]
            stats_alt, prom_alt = precomputar_stats_equipo(
                sesion, nombre_eq, datos_alt["id"], datos_alt["temporada"], rondas_alt, n=6
            )
            n_alt = prom_alt.get("n_partidos", 0) if prom_alt else 0
            if not n_alt:
                continue
            if progress_cb:
                progress_cb(f"✅ {nombre_eq}: +{n_alt} partidos de {nombre_alt}")
            if not prom_orig:
                return stats_alt, prom_alt
            # Merge: combina stats de ambas competencias ponderando por cantidad de partidos
            stats_merged = (stats_orig
                            + f"\n\n[Datos complementarios de {nombre_alt} — {n_alt} partidos]\n"
                            + stats_alt)
            return stats_merged, _merge_promedios(prom_orig, prom_alt, n_orig, n_alt)
        return stats_orig, prom_orig

    stats_eq1, prom1 = _buscar_en_otras_ligas(equipo1, stats_eq1, prom1)
    stats_eq2, prom2 = _buscar_en_otras_ligas(equipo2, stats_eq2, prom2)

    # Próximo partido entre los dos equipos.
    # #0m: usar UTC + offset del user para evitar inconsistencia con OS del server.
    ahora = datetime.utcnow().timestamp()
    _tz_h = get_tz_offset_hours()
    _hoy_user = (datetime.utcnow() + timedelta(hours=_tz_h)).date()
    inicio_hoy = (datetime(_hoy_user.year, _hoy_user.month, _hoy_user.day)
                  - timedelta(hours=_tz_h)).replace(tzinfo=timezone.utc).timestamp()
    evento_id_proximo = None
    info_ronda = ""

    for ronda_n in range(max(1, rondas - 1), rondas + 7):
        data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/"
                                 f"{liga_id}/season/{temporada_id}/events/round/{ronda_n}")
        for evento in data.get("events", []):
            home   = evento["homeTeam"]["name"]
            away   = evento["awayTeam"]["name"]
            status = evento.get("status", {}).get("type", "")
            start  = evento.get("startTimestamp", 0)
            eq1_ok = equipo1.lower() in home.lower() or equipo1.lower() in away.lower()
            eq2_ok = equipo2.lower() in home.lower() or equipo2.lower() in away.lower()
            es_hoy = inicio_hoy <= start < inicio_hoy + 86400
            es_vigente = (status == "inprogress"
                          or (status == "notstarted" and start > ahora)
                          or (status == "notstarted" and es_hoy))
            if eq1_ok and eq2_ok and es_vigente:
                evento_id_proximo = evento["id"]
                ri = evento.get("roundInfo", {})
                info_ronda = ri.get("name", "") or (f"Ronda {ri['round']}" if ri.get("round") else "")
                break
        if evento_id_proximo:
            break

    # Calcular líneas en Python
    _FOCO_A_CLAVE = {
        "corners": "ALL_Corner kicks", "corners_1h": "1ST_Corner kicks", "corners_2h": "2ND_Corner kicks",
        "goles": "goles",
        "tarjetas_amarillas": "ALL_Yellow cards", "tarjetas_amarillas_1h": "1ST_Yellow cards", "tarjetas_amarillas_2h": "2ND_Yellow cards",
        "remates": "ALL_Shots on target", "remates_1h": "1ST_Shots on target", "remates_2h": "2ND_Shots on target",
        "faltas": "ALL_Fouls", "faltas_1h": "1ST_Fouls", "faltas_2h": "2ND_Fouls",
    }
    lineas_python = {}
    for foco_key, stat_clave in _FOCO_A_CLAVE.items():
        v1 = prom1.get(stat_clave); v2 = prom2.get(stat_clave)
        if v1 is None or v2 is None:
            continue
        a1 = prom1.get(f"{stat_clave}_against"); a2 = prom2.get(f"{stat_clave}_against")
        total = (v1 + v2 + a1 + a2) / 2 if (a1 is not None and a2 is not None) else v1 + v2
        # #0l: la calibración de confianza ahora usa σ por stat
        # (corners=2.5, goles=1.5, etc.), no márgenes fijos.
        # Mantenemos margen_minimo=0.5 para goles como piso.
        mm = 0.5 if foco_key == "goles" else 1.0
        ld, ls, conf, lc = calcular_lineas_y_confianza(total, margen_minimo=mm, stat_key=foco_key)
        if _sc:
            tr = _sc.get_track_record(foco_key, liga_nombre, ls)
            conf, _ = _ajustar_confianza_por_track_record(foco_key, liga_nombre, ls, conf, tr)
        lineas_python[foco_key] = (total, ld, ls, conf, lc)

    lineas_ctx = []
    for fk, (tot, ld, ls, conf, lc) in lineas_python.items():
        ctx = (f"  {fk}: total esperado = {tot:.2f}"
               f" | línea directa = {ld}"
               f" | LÍNEA RECOMENDADA = {ls} (confianza: {conf})")
        if lc != ls:
            ctx += f" | LÍNEA CONSERVADORA = {lc} (confianza: Muy alta 🟢)"
        lineas_ctx.append(ctx)

    # ── BTTS (ambos anotan) ───────────────────────────────────────────
    btts1 = prom1.get("btts_score"); btts2 = prom2.get("btts_score")
    if btts1 is not None and btts2 is not None:
        btts_prob = min(btts1 * btts2, 0.95)   # cap: nunca mostrar 100% absoluto
        btts_rec  = "Sí" if btts_prob >= 0.50 else "No"
        _btts_dist = abs(btts_prob - 0.50)
        btts_conf = ("Alta 🟢"  if _btts_dist >= 0.20 else
                     "Media 🟡" if _btts_dist >= 0.10 else
                     "Baja 🔴")
        lineas_ctx.insert(0, (
            f"  btts (ambos anotan): P({equipo1} anota)={btts1*100:.0f}%"
            f" × P({equipo2} anota)={btts2*100:.0f}%"
            f" = {btts_prob*100:.0f}% | RECOMENDACIÓN = Ambos Anotan {btts_rec}"
            f" (confianza: {btts_conf})"
        ))

    # ── 1X2 con modelo Poisson ────────────────────────────────────────
    v1_g = prom1.get("goles"); v2_g = prom2.get("goles")
    a1_g = prom1.get("goles_against"); a2_g = prom2.get("goles_against")
    if all(x is not None for x in [v1_g, v2_g, a1_g, a2_g]):
        _HOME_ADV = 1.12
        af1 = prom1.get("attack_force");  df1 = prom1.get("defense_force")
        af2 = prom2.get("attack_force");  df2 = prom2.get("defense_force")
        lg  = (prom1.get("league_avg_goals", 1.2) + prom2.get("league_avg_goals", 1.2)) / 2
        if all(x is not None for x in [af1, df1, af2, df2]):
            # Dixon-Coles: xG = ataque_propio × debilidad_defensiva_rival × promedio_liga
            # df > 1 = defensa débil (concede más) → rival anota más ✓
            # df < 1 = defensa sólida (concede menos) → rival anota menos ✓
            xg1 = af1 * df2 * lg * _HOME_ADV
            xg2 = af2 * df1 * lg / _HOME_ADV
        else:
            xg1 = (v1_g + a2_g) / 2 * _HOME_ADV
            xg2 = (v2_g + a1_g) / 2 / _HOME_ADV
        p_loc, p_emp, p_vis = calcular_1x2(xg1, xg2)
        max_res = max(
            [("1 (local)", p_loc), ("X (empate)", p_emp), ("2 (visitante)", p_vis)],
            key=lambda x: x[1]
        )
        conf_1x2 = ("Alta 🟢"  if max_res[1] >= 0.50 else
                    "Media 🟡" if max_res[1] >= 0.35 else
                    "Baja 🔴")
        # Bug #0g: nombres explícitos en el ctx del LLM, no "el local".
        nombre_max = (equipo1 if max_res[0].startswith("1") else
                      equipo2 if max_res[0].startswith("2") else "Empate")
        rec_label  = (f"{equipo1} (local)"     if max_res[0].startswith("1") else
                      f"{equipo2} (visitante)" if max_res[0].startswith("2") else
                      "empate")
        lineas_ctx.insert(0, (
            f"  1x2: {equipo1} (local) {p_loc*100:.0f}% | empate {p_emp*100:.0f}% | "
            f"{equipo2} (visitante) {p_vis*100:.0f}%"
            f" | xG: {equipo1}={xg1:.2f} / {equipo2}={xg2:.2f}"
            f" | RECOMENDACIÓN = {rec_label} (confianza: {conf_1x2})"
        ))

    contexto = (
        "DATOS REALES DE SOFASCORE (promedios ya calculados por equipo):\n\n"
        f"{stats_eq1}\n\n{stats_eq2}\n\n"
        "LÍNEAS DE APUESTA PRE-CALCULADAS POR PYTHON:\n"
        "  (directa = más agresiva | RECOMENDADA = margen ≥ 1.0 | CONSERVADORA = margen ≥ 2.5, Muy Alta)\n"
        + ("\n".join(lineas_ctx) if lineas_ctx else "  (sin datos suficientes)") + "\n"
    )
    return contexto, evento_id_proximo, info_ronda, {"id": liga_id, "temporada": temporada_id, "rondas": rondas}, lineas_python, prom1, prom2


# ── Corners antes del minuto X ────────────────────────────────────────
# SofaScore no expone el minuto exacto de cada corner en su API pública.
# Usamos interpolación lineal sobre los datos de 1T y 2T que ya tenemos.

def _interpolar_corners(prom: dict, minuto: int) -> tuple:
    """
    Estima corners generados/concedidos antes del minuto X
    por interpolación lineal sobre promedios de 1T y 2T.
    """
    c1g = prom.get("1ST_Corner kicks")
    c1a = prom.get("1ST_Corner kicks_against")
    c2g = prom.get("2ND_Corner kicks")
    c2a = prom.get("2ND_Corner kicks_against")

    if minuto <= 45:
        frac = minuto / 45
        gen = c1g * frac if c1g is not None else None
        con = c1a * frac if c1a is not None else None
    else:
        frac2 = (minuto - 45) / 45
        gen = (c1g + c2g * frac2) if (c1g is not None and c2g is not None) else None
        con = (c1a + c2a * frac2) if (c1a is not None and c2a is not None) else None

    return gen, con


def hacer_analisis_corners_tiempo(equipo1: str, equipo2: str, minuto: int,
                                   liga_nombre: str, progress_cb=None):
    """
    Análisis de corners antes del minuto X usando interpolación lineal
    sobre los promedios de 1T/2T de SofaScore.
    Retorna (contexto, lineas_python, prom1, prom2).
    """
    _liga_nombre_oficial, liga = buscar_liga_info(liga_nombre)
    if not liga:
        return "(sin datos suficientes)\n", {}, {}, {}

    if _liga_nombre_oficial:
        liga_nombre = _liga_nombre_oficial
    liga_id      = liga["id"]
    temporada_id = liga["temporada"]
    rondas       = liga["rondas"]
    sesion       = _nueva_sesion()

    try:
        rd = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/season/{temporada_id}/rounds")
        rl = rd.get("rounds", [])
        rondas = rd.get("currentRound", {}).get("round") or (rl[-1].get("round", rondas) if rl else rondas)
    except Exception:
        pass

    if progress_cb: progress_cb(f"🔍 Bajando stats de {equipo1} y {equipo2}...")
    _, prom_raw1 = precomputar_stats_equipo(sesion, equipo1, liga_id, temporada_id, rondas)
    _, prom_raw2 = precomputar_stats_equipo(sesion, equipo2, liga_id, temporada_id, rondas)

    foco_key = f"corners_antes_{minuto}"
    gen1, con1 = _interpolar_corners(prom_raw1, minuto)
    gen2, con2 = _interpolar_corners(prom_raw2, minuto)

    prom1, prom2 = {}, {}
    if gen1 is not None: prom1[foco_key]              = gen1
    if con1 is not None: prom1[f"{foco_key}_against"] = con1
    if gen2 is not None: prom2[foco_key]              = gen2
    if con2 is not None: prom2[f"{foco_key}_against"] = con2

    lineas_python = {}
    v1, v2 = prom1.get(foco_key), prom2.get(foco_key)
    if v1 is not None and v2 is not None:
        a1, a2 = prom1.get(f"{foco_key}_against"), prom2.get(f"{foco_key}_against")
        total = (v1 + v2 + a1 + a2) / 2 if (a1 is not None and a2 is not None) else v1 + v2
        d, s, c, cons = calcular_lineas_y_confianza(total, stat_key=foco_key)  # #0l
        lineas_python[foco_key] = (total, d, s, c, cons)

    def _fmt(v):
        return f"{v:.2f}" if v is not None else "(sin datos)"

    nota = "(estimado por interpolación lineal sobre promedios de 1T/2T)"
    contexto = (
        f"CORNERS ANTES DEL MINUTO {minuto} {nota}:\n\n"
        f"  {equipo1}: genera {_fmt(gen1)}, concede {_fmt(con1)}\n"
        f"  {equipo2}: genera {_fmt(gen2)}, concede {_fmt(con2)}\n\n"
        "LÍNEAS PRE-CALCULADAS:\n"
        "  (directa = más agresiva | RECOMENDADA = margen ≥ 1.0 | CONSERVADORA = margen ≥ 2.5)\n"
    )
    if foco_key in lineas_python:
        total, d, s, c, cons = lineas_python[foco_key]
        ctx_l = (
            f"  {foco_key}: total esperado = {total:.2f}"
            f" | línea directa = {d}"
            f" | LÍNEA RECOMENDADA = {s} (confianza: {c})"
        )
        if cons != s:
            ctx_l += f" | LÍNEA CONSERVADORA = {cons} (confianza: Muy alta 🟢)"
        contexto += ctx_l + "\n"
    else:
        contexto += "  (sin datos suficientes)\n"

    return contexto, lineas_python, prom1, prom2


_STAT_GEN_LABEL = {
    "corners":               "corners",
    "corners_1h":            "corners en el 1er tiempo",
    "corners_2h":            "corners en el 2do tiempo",
    "goles":                 "goles",
    "tarjetas_amarillas":    "tarjetas amarillas",
    "tarjetas_amarillas_1h": "amarillas en el 1er tiempo",
    "tarjetas_amarillas_2h": "amarillas en el 2do tiempo",
    "remates":               "remates al arco",
    "remates_1h":            "remates al arco en el 1er tiempo",
    "remates_2h":            "remates al arco en el 2do tiempo",
    "faltas":                "faltas",
    "faltas_1h":             "faltas en el 1er tiempo",
    "faltas_2h":             "faltas en el 2do tiempo",
}

_FOCO_A_CLAVE_STAT = {
    "corners":               "ALL_Corner kicks",
    "corners_1h":            "1ST_Corner kicks",
    "corners_2h":            "2ND_Corner kicks",
    "goles":                 "goles",
    "tarjetas_amarillas":    "ALL_Yellow cards",
    "tarjetas_amarillas_1h": "1ST_Yellow cards",
    "tarjetas_amarillas_2h": "2ND_Yellow cards",
    "remates":               "ALL_Shots on target",
    "remates_1h":            "1ST_Shots on target",
    "remates_2h":            "2ND_Shots on target",
    "faltas":                "ALL_Fouls",
    "faltas_1h":             "1ST_Fouls",
    "faltas_2h":             "2ND_Fouls",
}

def _generar_parrafos_python(foco: str, eq1: str, eq2: str,
                              lineas_python: dict, prom1: dict, prom2: dict,
                              liga_nombre: str = "") -> str | None:
    if foco not in lineas_python:
        return None
    total, directa, recomendada, confianza, conservadora = lineas_python[foco]

    # Foco dinámico: corners_antes_X
    if foco.startswith("corners_antes_"):
        minuto = foco.replace("corners_antes_", "")
        stat_label = f"corners antes del minuto {minuto}"
        stat_clave = foco
    else:
        stat_clave = _FOCO_A_CLAVE_STAT.get(foco)
        stat_label = _STAT_GEN_LABEL.get(foco, foco)

    if stat_clave:
        v1 = prom1.get(stat_clave)
        v2 = prom2.get(stat_clave)
        a1 = prom1.get(f"{stat_clave}_against")
        a2 = prom2.get(f"{stat_clave}_against")
        if v1 is not None and v2 is not None:
            p1 = f"{eq1} genera {v1:.2f} {stat_label}"
            if a1 is not None:
                p1 += f" y concede {a1:.2f}"
            p1 += f", {eq2} genera {v2:.2f}"
            if a2 is not None:
                p1 += f" y concede {a2:.2f}"
            p1 += f". El total esperado es {total:.2f}."
        else:
            p1 = f"El total esperado de {stat_label} es {total:.2f}."
    else:
        p1 = f"El total esperado de {stat_label} es {total:.2f}."

    p2 = f"La línea directa es {directa}. La apuesta recomendada es {recomendada} ({confianza})."
    if conservadora != recomendada:
        p2 += f" Para los más conservadores: {conservadora} (Muy alta)."

    tr_texto = ""
    if _sc:
        tr = _sc.get_track_record(foco, liga_nombre or None, recomendada)
        if tr:
            nivel_desc = {
                "C": f"{tr['liga']} | {tr['rango']}",
                "B": tr["liga"],
                "A": "todos los partidos",
            }
            tr_texto = (
                f"\nTRACK RECORD COLECTIVO (foco: {foco} | {nivel_desc[tr['nivel']]}):\n"
                f"  {tr['muestras']} predicciones verificadas → {tr['aciertos']} aciertos "
                f"({round(tr['tasa']*100)}%)\n"
            )

    return f"{p1}\n\n{p2}{tr_texto}"


def buscar_fixture_equipo(nombre_equipo: str, dias: int = 4) -> list[str]:
    sesion     = _nueva_sesion()
    ahora      = datetime.utcnow().timestamp()  # #0m: explícitamente UTC
    # "Hoy" en la TZ del usuario (viene del header / body del request).
    _tz_offset  = get_tz_offset_hours()
    _hoy_local  = (datetime.utcnow() + timedelta(hours=_tz_offset)).date()
    # #0m: medianoche del "hoy local" expresada en UTC, independiente del OS del server.
    # Antes usaba datetime(...).timestamp() que asume TZ del OS → roto en Render (UTC) vs dev.
    inicio_hoy  = (datetime(_hoy_local.year, _hoy_local.month, _hoy_local.day)
                   - timedelta(hours=_tz_offset)).replace(tzinfo=timezone.utc).timestamp()
    resultados = []
    vistos     = set()
    deadline   = time.time() + 20  # máximo 20 segundos en total

    def _agregar(evento, nombre_torneo=None):
        eid = evento.get("id")
        if eid in vistos: return
        home = evento.get("homeTeam", {}).get("name", "")
        away = evento.get("awayTeam", {}).get("name", "")
        if nombre_equipo.lower() not in home.lower() and nombre_equipo.lower() not in away.lower(): return
        ts = evento.get("startTimestamp", 0)
        st = evento.get("status", {}).get("type", "")
        es_hoy = inicio_hoy <= ts < inicio_hoy + 86400
        es_futuro = (st == "inprogress" or (st == "notstarted" and ts > ahora) or (st == "notstarted" and es_hoy))
        if not es_futuro: return
        vistos.add(eid)
        torneo = nombre_torneo or evento.get("tournament", {}).get("uniqueTournament", {}).get("name", "")
        # #0m: usar UTC + offset del user (consistente con resto del sistema).
        if ts:
            _tz_h = get_tz_offset_hours()
            hora_str = (datetime.utcfromtimestamp(ts) + timedelta(hours=_tz_h)).strftime("%d/%m/%Y %H:%M")
        else:
            hora_str = "horario sin confirmar"
        hoy_tag   = " [HOY]"      if es_hoy else ""
        curso_tag = " [EN CURSO]" if st == "inprogress" else ""
        resultados.append(f"{home} vs {away} — {torneo} — {hora_str}{hoy_tag}{curso_tag}")

    for delta in range(dias):
        if time.time() > deadline:
            break
        fecha_api = (_hoy_local + timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{fecha_api}")
            for e in data.get("events", []): _agregar(e)
        except Exception: pass

    if not resultados:
        for nombre_liga, datos_liga in LIGAS.items():
            if time.time() > deadline:
                break
            base = (f"https://www.sofascore.com/api/v1/unique-tournament"
                    f"/{datos_liga['id']}/season/{datos_liga['temporada']}/events")
            for endpoint in ["last/0", "next/0"]:
                if time.time() > deadline:
                    break
                try:
                    resp = fetch_api(sesion, f"{base}/{endpoint}")
                    for e in resp.get("events", []): _agregar(e, nombre_torneo=nombre_liga)
                except Exception: pass

    return resultados


# ── Combinada helpers ────────────────────────────────────────────────

_STATS_COMBINADA = [
    ("corners",            "ALL_Corner kicks"),
    ("goles",              "goles"),
    ("tarjetas_amarillas", "ALL_Yellow cards"),
    ("faltas",             "ALL_Fouls"),
    ("remates",            "ALL_Shots on target"),
]

# Líneas mínimas que combinada AUTO acepta para ofrecer un pick:
# - Originalmente filtraban líneas demasiado bajas para apostar.
# - #8: bajadas para no excluir equipos con poco historial.
# - #0l: con la nueva calibración por σ, las líneas Alta confianza ya
#        respetan la variabilidad real. El threshold acá funciona como
#        "piso de utilidad" — líneas Over X.5 tan bajas que no aportan
#        valor para apostar (ej: Over 0.5 tarjetas no es útil).
# Ajustado: tarjetas_amarillas (0.5→1.5: Over 0.5 era trivial),
#           faltas (8.5→10.5: con σ=3 el piso útil es ~10.5).
_LINEA_MINIMA_COMBINADA: dict[str, float] = {
    "goles": 0.5, "corners": 3.5, "tarjetas_amarillas": 1.5,
    "tarjetas_rojas": 0.5, "remates": 2.5, "faltas": 10.5,
}

_ORDEN_CONFIANZA = {"Muy alta 🟢": 0, "Alta 🟢": 1, "Media 🟡": 2, "Baja 🔴": 3}

_STAT_NOMBRE_ES = {
    "corners": "Corners totales", "corners_1h": "Corners 1er tiempo", "corners_2h": "Corners 2do tiempo",
    "goles": "Goles totales", "tarjetas_amarillas": "Tarjetas amarillas",
    "tarjetas_amarillas_1h": "Amarillas 1er tiempo", "tarjetas_amarillas_2h": "Amarillas 2do tiempo",
    "tarjetas_rojas": "Tarjetas rojas", "faltas": "Faltas totales",
    "faltas_1h": "Faltas 1er tiempo", "faltas_2h": "Faltas 2do tiempo",
    "remates": "Remates al arco", "remates_1h": "Remates al arco 1T", "remates_2h": "Remates al arco 2T",
    "btts":              "Ambos anotan",
    "1x2":               "Resultado (1X2)",
    "doble_oportunidad": "Doble oportunidad",
}

_STATS_COMBINADA_MAPA = {
    "corners": "ALL_Corner kicks", "corners_1h": "1ST_Corner kicks", "corners_2h": "2ND_Corner kicks",
    "goles": "goles",
    "tarjetas_amarillas": "ALL_Yellow cards", "tarjetas_amarillas_1h": "1ST_Yellow cards", "tarjetas_amarillas_2h": "2ND_Yellow cards",
    "tarjetas_rojas": "ALL_Red cards",
    "faltas": "ALL_Fouls", "faltas_1h": "1ST_Fouls", "faltas_2h": "2ND_Fouls",
    "remates": "ALL_Shots on target", "remates_1h": "1ST_Shots on target", "remates_2h": "2ND_Shots on target",
}


def _parsear_partidos_fixtures() -> list[tuple]:
    start = SYSTEM_PROMPT.find("=== PRÓXIMOS PARTIDOS")
    if start == -1: return []
    next_sec = SYSTEM_PROMPT.find("\n===", start + 5)
    fixtures_txt = SYSTEM_PROMPT[start:next_sec] if next_sec != -1 else SYSTEM_PROMPT[start:]

    ahora_utc = datetime.utcnow()
    resultados = []; liga_actual = ""
    for linea in fixtures_txt.splitlines():
        ls = linea.strip()
        if ls.endswith(":") and not ls.startswith("-") and "===" not in ls:
            candidato = ls[:-1].strip()
            if candidato: liga_actual = candidato
            continue
        m = re.search(r'-\s+(.+?)\s+vs\s+(.+?)\s+\(', linea)
        if m and liga_actual:
            # #0q: Filtrar partidos que ya pasaron. El SYSTEM_PROMPT usa UTC
            # (post #0m) → comparar directo contra utcnow().
            # Los [EN CURSO] siempre se incluyen (partido empezado pero no terminado).
            if "[EN CURSO]" not in linea:
                mf = re.search(r'\((\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})', linea)
                if mf:
                    try:
                        dt_partido = datetime.strptime(
                            f"{mf.group(1)} {mf.group(2)}", "%d/%m/%Y %H:%M"
                        )
                        if dt_partido < ahora_utc:
                            continue  # partido ya pasó
                    except ValueError:
                        continue  # fecha malformada → excluir por precaución
            home = m.group(1).strip(); away = m.group(2).strip()
            es_prio = "[HOY]" in linea or "[EN CURSO]" in linea
            resultados.append((home, away, liga_actual, es_prio))

    resultados.sort(key=lambda x: 0 if x[3] else 1)
    return resultados


def _retag_fixtures_para_tz(texto: str, user_tz_hours: float) -> str:
    """
    Re-formatea las horas (DD/MM/YYYY HH:MM) y el tag [HOY] del bloque de
    fixtures para que coincidan con la TZ del usuario. Los fixtures se
    cargan al arrancar con la TZ del servidor (_SERVER_TZ_AT_LOAD); acá
    revertimos a UTC y reconvertimos al TZ del usuario.
    """
    delta = user_tz_hours - _SERVER_TZ_AT_LOAD
    user_hoy = (datetime.utcnow() + timedelta(hours=user_tz_hours)).date()
    user_hoy_str = user_hoy.strftime("%d/%m/%Y")

    def _fix_linea(linea: str) -> str:
        sin_hoy = re.sub(r'\s*\[HOY\]', '', linea)
        if delta != 0:
            def _shift(m):
                try:
                    dt = datetime.strptime(
                        f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M"
                    ) + timedelta(hours=delta)
                    return f"({dt.strftime('%d/%m/%Y %H:%M')}"
                except ValueError:
                    return m.group(0)
            sin_hoy = re.sub(
                r'\((\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})',
                _shift, sin_hoy, count=1,
            )
        # Re-aplicar [HOY] si la fecha del partido (ya en TZ del usuario)
        # cae en el día de hoy del usuario.
        m = re.search(r'\((\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}', sin_hoy)
        if m and m.group(1) == user_hoy_str:
            # Insertar [HOY] justo después del HH:MM, antes de [EN CURSO]/")".
            sin_hoy = re.sub(
                r'(\d{2}:\d{2})(\s*(?:\[EN CURSO\])?\))',
                r'\1 [HOY]\2', sin_hoy, count=1,
            )
        return sin_hoy

    return "\n".join(_fix_linea(l) for l in texto.splitlines())


def _obtener_fixtures_texto() -> str:
    start = SYSTEM_PROMPT.find("=== PRÓXIMOS PARTIDOS")
    if start == -1: return ""
    next_sec = SYSTEM_PROMPT.find("\n===", start + 5)
    bruto = SYSTEM_PROMPT[start:next_sec] if next_sec != -1 else SYSTEM_PROMPT[start:]
    user_tz = get_tz_offset_hours()
    if user_tz == _SERVER_TZ_AT_LOAD:
        return bruto
    return _retag_fixtures_para_tz(bruto, user_tz)


def _system_prompt_con_tz() -> str:
    """
    #0m completo: Devuelve SYSTEM_PROMPT con el bloque de fixtures
    re-tageado según el offset del usuario actual. SYSTEM_PROMPT se
    guarda en UTC base, pero el LLM debe ver los horarios en la TZ
    del user. Esta función debe usarse en TODAS las llamadas a Groq.
    """
    if not SYSTEM_PROMPT:
        return SYSTEM_PROMPT
    user_tz = get_tz_offset_hours()
    if user_tz == _SERVER_TZ_AT_LOAD:
        return SYSTEM_PROMPT
    start = SYSTEM_PROMPT.find("=== PRÓXIMOS PARTIDOS")
    if start == -1:
        return SYSTEM_PROMPT
    next_sec = SYSTEM_PROMPT.find("\n===", start + 5)
    end = next_sec if next_sec != -1 else len(SYSTEM_PROMPT)
    bloque_utc = SYSTEM_PROMPT[start:end]
    bloque_tz = _retag_fixtures_para_tz(bloque_utc, user_tz)
    return SYSTEM_PROMPT[:start] + bloque_tz + SYSTEM_PROMPT[end:]


def _buscar_en_fixtures_cargados(nombre_equipo: str) -> list[str]:
    fixtures_texto = _obtener_fixtures_texto()
    if not fixtures_texto: return []
    resultados = []
    for linea in fixtures_texto.splitlines():
        if nombre_equipo.lower() in linea.lower() and ' vs ' in linea:
            es_hoy_f = '[HOY]' in linea; en_curso_f = '[EN CURSO]' in linea
            limpia = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', '', linea).strip().lstrip('- ').strip()
            sufijo = ' (hoy)' if es_hoy_f else ''
            sufijo += ' — en curso' if en_curso_f else ''
            resultados.append(f"{limpia}{sufijo}")
    return resultados


def _calcular_picks_partido(sesion, eq1: str, eq2: str, liga_nombre: str,
                             stats_keys: list | None = None) -> list[dict]:
    _liga_nombre_oficial, liga = buscar_liga_info(liga_nombre)
    if not liga: return []

    if _liga_nombre_oficial:
        liga_nombre = _liga_nombre_oficial
    liga_id = liga["id"]; temporada_id = liga["temporada"]; rondas = liga["rondas"]

    try:
        rd = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/"
                               f"{liga_id}/season/{temporada_id}/rounds")
        rl = rd.get("rounds", [])
        rondas = rd.get("currentRound", {}).get("round") or (rl[-1].get("round", rondas) if rl else rondas)
    except Exception:
        pass

    try:
        _, prom1 = precomputar_stats_equipo(sesion, eq1, liga_id, temporada_id, rondas)
        _, prom2 = precomputar_stats_equipo(sesion, eq2, liga_id, temporada_id, rondas)
    except Exception:
        return []

    stats_a_evaluar = (
        _STATS_COMBINADA if stats_keys is None
        else [(k, _STATS_COMBINADA_MAPA[k]) for k in stats_keys if k in _STATS_COMBINADA_MAPA]
    )

    picks = []
    for stat_key, stat_clave in stats_a_evaluar:
        v1 = prom1.get(stat_clave); v2 = prom2.get(stat_clave)
        if v1 is None or v2 is None: continue
        a1 = prom1.get(f"{stat_clave}_against"); a2 = prom2.get(f"{stat_clave}_against")
        total = (v1 + v2 + a1 + a2) / 2 if (a1 is not None and a2 is not None) else v1 + v2

        ld, ls, conf, _ = calcular_lineas_y_confianza(total, stat_key=stat_key)  # #0l

        if stats_keys is None:
            minima = _LINEA_MINIMA_COMBINADA.get(stat_key.split("_")[0], 0.5)
            if float(ls.replace("Over ", "")) < minima: continue

        picks.append({
            "partido": f"{eq1} vs {eq2}", "equipo1": eq1, "equipo2": eq2,
            "liga": liga_nombre, "stat": stat_key, "total": total,
            "linea_directa": ld, "linea_segura": ls, "confianza": conf,
        })

    # ── btts (ambos anotan) ──────────────────────────────────────────────────
    if stats_keys is None:
        btts1 = prom1.get("btts_score")
        btts2 = prom2.get("btts_score")
        if btts1 is not None and btts2 is not None:
            btts_prob = min(btts1 * btts2, 0.95)
            if btts_prob >= 0.65:
                btts_rec  = "Ambos Anotan Sí" if btts_prob >= 0.50 else "Ambos Anotan No"
                btts_conf = "Alta 🟢" if btts_prob >= 0.75 else "Media 🟡"
                if _sc:
                    tr = _sc.get_track_record("btts", liga_nombre, btts_rec)
                    btts_conf, _ = _ajustar_confianza_por_track_record(
                        "btts", liga_nombre, btts_rec, btts_conf, tr
                    )
                picks.append({
                    "partido": f"{eq1} vs {eq2}", "equipo1": eq1, "equipo2": eq2,
                    "liga": liga_nombre, "stat": "btts",
                    "total": round(btts_prob * 100, 1),
                    "linea_directa": btts_rec, "linea_segura": btts_rec,
                    "confianza": btts_conf,
                })

    # ── 1x2 y doble oportunidad ──────────────────────────────────────────────
    if stats_keys is None:
        xg1 = prom1.get("goles")
        xg2 = prom2.get("goles")
        if xg1 is not None and xg2 is not None:
            p_loc, p_emp, p_vis = calcular_1x2(xg1, xg2)
            candidatos_1x2 = [
                ("Local gana",     p_loc),
                ("Empate",         p_emp),
                ("Visitante gana", p_vis),
            ]
            mejor_label, mejor_prob = max(candidatos_1x2, key=lambda x: x[1])
            if mejor_prob >= 0.55:
                conf_1x2 = "Alta 🟢" if mejor_prob >= 0.65 else "Media 🟡"
                if _sc:
                    tr = _sc.get_track_record("1x2", liga_nombre, mejor_label)
                    conf_1x2, _ = _ajustar_confianza_por_track_record(
                        "1x2", liga_nombre, mejor_label, conf_1x2, tr
                    )
                picks.append({
                    "partido": f"{eq1} vs {eq2}", "equipo1": eq1, "equipo2": eq2,
                    "liga": liga_nombre, "stat": "1x2",
                    "total": round(mejor_prob * 100, 1),
                    "linea_directa": mejor_label, "linea_segura": mejor_label,
                    "confianza": conf_1x2,
                })

            doble_ops = [
                ("1X", p_loc + p_emp),
                ("X2", p_emp + p_vis),
                ("12", p_loc + p_vis),
            ]
            mejor_do, prob_do = max(doble_ops, key=lambda x: x[1])
            if prob_do >= 0.75:
                conf_do = "Alta 🟢" if prob_do >= 0.80 else "Media 🟡"
                if _sc:
                    tr = _sc.get_track_record("doble_oportunidad", liga_nombre, mejor_do)
                    conf_do, _ = _ajustar_confianza_por_track_record(
                        "doble_oportunidad", liga_nombre, mejor_do, conf_do, tr
                    )
                picks.append({
                    "partido": f"{eq1} vs {eq2}", "equipo1": eq1, "equipo2": eq2,
                    "liga": liga_nombre, "stat": "doble_oportunidad",
                    "total": round(prob_do * 100, 1),
                    "linea_directa": mejor_do, "linea_segura": mejor_do,
                    "confianza": conf_do,
                })

    return picks


def hacer_combinada_auto(n_picks: int = 2, progress_cb=None, liga_filtro: str = "") -> tuple[list[dict], dict]:
    partidos = _parsear_partidos_fixtures()
    if not partidos:
        # #0q: distinguir "sin fixtures cargados" de "todos los fixtures ya pasaron".
        # _parsear_partidos_fixtures filtra temporalmente — si SYSTEM_PROMPT tiene
        # líneas con " vs " en el bloque de partidos, es que ya jugaron.
        _start = SYSTEM_PROMPT.find("=== PRÓXIMOS PARTIDOS")
        _tiene_fixtures_cargados = _start != -1 and " vs " in SYSTEM_PROMPT[_start:]
        return [], {"n_liga": 0, "n_analizados": 0, "partidos": [],
                    "todos_jugados": _tiene_fixtures_cargados}

    # #0k: ligas pedidas (multi-liga separadas por coma, ej:
    # "Premier League,Besta deild karla") + tracking de cuáles
    # tienen/no tienen partidos para mostrar info de receso.
    ligas_pedidas_oficiales: list[str] = []
    ligas_sin_partidos: list[str] = []

    if liga_filtro:
        # Soportar múltiples ligas separadas por coma. Cada token se
        # normaliza individualmente (#0i: alias → nombre oficial).
        tokens = [s.strip() for s in liga_filtro.split(",") if s.strip()]
        for tok in tokens:
            for nom in normalizar_liga(tok):
                if nom not in ligas_pedidas_oficiales:
                    ligas_pedidas_oficiales.append(nom)

        if not ligas_pedidas_oficiales:
            return [], {"n_liga": 0, "n_analizados": 0, "partidos": [],
                        "liga_no_reconocida": liga_filtro}

        # Filtrar partidos por cualquiera de las ligas pedidas.
        partidos_filtrados = [p for p in partidos if p[2] in ligas_pedidas_oficiales]

        # Identificar ligas pedidas que NO produjeron partidos →
        # potencialmente en receso o sin carga. estado_liga() lo dirá.
        ligas_con_partidos = {p[2] for p in partidos_filtrados}
        ligas_sin_partidos = [l for l in ligas_pedidas_oficiales
                              if l not in ligas_con_partidos]

        partidos = partidos_filtrados

    if not partidos:
        # No hay partidos para ninguna de las ligas pedidas.
        # Devolver info de receso para que el formateador la use.
        estados = {}
        for l in ligas_sin_partidos:
            estados[l] = estado_liga(l)
        return [], {"n_liga": 0, "n_analizados": 0, "partidos": [],
                    "ligas_sin_partidos": ligas_sin_partidos,
                    "estados_ligas": estados,
                    "ligas_pedidas": ligas_pedidas_oficiales}

    n_liga = len(partidos)
    partidos_hoy = [p for p in partidos if p[3]]
    # Priorizar partidos de hoy/en curso, pero si hay menos de 3 completar
    # con los próximos para no quedar atrapado en una sola liga (ej: solo
    # la Liga 1 Perú juega de noche en hora ARG → era el único [HOY]).
    if len(partidos_hoy) >= 3:
        candidatos = partidos_hoy
    else:
        resto      = [p for p in partidos if not p[3]]
        candidatos = partidos_hoy + resto

    sesion = _nueva_sesion(); todos_picks = []; partidos_analizados = []

    for i, (home, away, liga_nombre, _) in enumerate(candidatos[:4]):
        if progress_cb:
            progress_cb(f"🔍 Analizando partido {i+1}/{min(len(candidatos), 4)}: {home} vs {away}...")
        partidos_analizados.append(f"{home} vs {away} ({liga_nombre})")
        picks = _calcular_picks_partido(sesion, home, away, liga_nombre)
        todos_picks.extend(picks)

    debug_info = {"n_liga": n_liga, "n_analizados": len(partidos_analizados),
                  "partidos": partidos_analizados,
                  # #0k: pasamos info de receso aunque haya picks para que
                  # el formateador agregue una nota informativa.
                  "ligas_sin_partidos": ligas_sin_partidos,
                  "estados_ligas": {l: estado_liga(l) for l in ligas_sin_partidos},
                  "ligas_pedidas": ligas_pedidas_oficiales}

    if not todos_picks:
        return [], debug_info

    todos_picks.sort(key=lambda x: _ORDEN_CONFIANZA.get(x["confianza"], 99))

    picks_finales = []; partidos_usados = set(); stats_por_partido = {}

    for pick in todos_picks:
        p = pick["partido"]
        if p not in partidos_usados:
            picks_finales.append(pick); partidos_usados.add(p); stats_por_partido[p] = [pick["stat"]]
        if len(picks_finales) >= n_picks: break

    if len(picks_finales) < n_picks:
        for pick in todos_picks:
            if pick in picks_finales: continue
            p, s = pick["partido"], pick["stat"]
            if s not in stats_por_partido.get(p, []):
                picks_finales.append(pick); stats_por_partido.setdefault(p, []).append(s)
            if len(picks_finales) >= n_picks: break

    return picks_finales[:max(n_picks, 2)], debug_info


def hacer_combinada_especifica(partidos_picks: list[tuple]) -> list[dict]:
    sesion = _nueva_sesion(); todos_picks = []
    for eq1, eq2, stats_pedidas, liga_nombre in partidos_picks:
        keys = None if stats_pedidas == ["auto"] else stats_pedidas
        todos_picks.extend(_calcular_picks_partido(sesion, eq1, eq2, liga_nombre, keys))
    todos_picks.sort(key=lambda x: _ORDEN_CONFIANZA.get(x["confianza"], 99))
    return todos_picks


def _formatear_combinada(picks: list[dict], liga_filtro: str = "", debug_info: dict | None = None) -> str:
    info = debug_info or {}
    estados = info.get("estados_ligas", {}) or {}
    ligas_sin_partidos = info.get("ligas_sin_partidos", []) or []

    if not picks:
        liga_msg = f" de {liga_filtro}" if liga_filtro else ""
        n_liga = info.get("n_liga", -1); n_anal = info.get("n_analizados", 0); partidos = info.get("partidos", [])

        # #0k: Si hay ligas en receso, mensaje específico con próximos partidos.
        if ligas_sin_partidos:
            lineas = []
            for l in ligas_sin_partidos:
                est = estados.get(l, {})
                lineas.append("• " + est.get('mensaje', f"{l}: sin partidos disponibles."))
            return ("No pude armar una combinada porque las ligas pedidas no tienen partidos hoy:\n"
                    + "\n".join(lineas)
                    + "\n\nProbá con otra liga que esté activa.")

        if info.get("liga_no_reconocida"):
            return (f"No reconocí la liga '{info['liga_no_reconocida']}'. "
                    "Ligas disponibles: " + ", ".join(sorted(LIGAS.keys())) + ".")

        if n_liga == 0:
            # #0q: mensaje diferenciado según si los fixtures ya pasaron o no se cargaron.
            if info.get("todos_jugados"):
                return ("Los partidos que tenía cargados ya se jugaron. "
                        "No quedan partidos pendientes para hoy. "
                        "¿Querés que busque picks para partidos de mañana o de otra liga?")
            return (f"No hay partidos{liga_msg} cargados en los fixtures. "
                    "Puede que la liga no tenga partidos próximos o que no se hayan podido cargar al arrancar.")
        elif n_anal > 0:
            lista = "\n".join(f"  • {p}" for p in partidos)
            return (f"Analicé {n_anal} partido(s){liga_msg} pero ninguno generó picks con suficiente confianza:\n"
                    f"{lista}\nProbá pedir una combinada específica indicando el partido y las stats.")
        else:
            return (f"No encontré picks{liga_msg} con suficiente confianza para armar una combinada. "
                    "Puede que no haya fixtures cargados para hoy, o que los datos no sean suficientes.")

    _PROB = {"Muy alta 🟢": 0.88, "Alta 🟢": 0.73, "Media 🟡": 0.58, "Baja 🔴": 0.42}
    prob = 1.0
    for p in picks: prob *= _PROB.get(p["confianza"], 0.50)
    conf_comb = ("Muy alta 🟢" if prob >= 0.72 else "Alta 🟢" if prob >= 0.55 else "Media 🟡" if prob >= 0.38 else "Baja 🔴")

    lineas = [f"🎯 APUESTA COMBINADA ({len(picks)} selecciones)\n"]
    for i, pick in enumerate(picks, 1):
        sn = _STAT_NOMBRE_ES.get(pick["stat"], pick["stat"])
        lineas.append(f"\nSelección {i}: {pick['linea_segura']} {sn}\n"
                      f"  {pick['equipo1']} vs {pick['equipo2']} — {pick['liga']}\n"
                      f"  Total esperado: {pick['total']:.2f} | Confianza: {pick['confianza']}\n"
                      f"  (línea directa: {pick['linea_directa']})")

    lineas.append(f"\n📊 Confianza combinada: {conf_comb} (prob. estimada: {prob*100:.0f}% — {len(picks)} selecciones multiplicadas)")
    lineas.append("⚠️ Todas las selecciones deben entrar para ganar. A mayor número de picks, menor probabilidad combinada.")
    lineas.append("⚠️ Solo una recomendación estadística. Los resultados pueden variar.")

    # #0k: Si parte de las ligas pedidas no produjo partidos, avisar al user
    # cuáles están en receso/no disponibles y por qué.
    if ligas_sin_partidos:
        lineas.append("")  # línea en blanco
        lineas.append("ℹ️ Algunas ligas pedidas no tenían partidos disponibles:")
        for l in ligas_sin_partidos:
            est = estados.get(l, {})
            lineas.append("  • " + est.get('mensaje', f"{l}: sin partidos disponibles."))

    return "\n".join(lineas)


def _guardar_picks_combinada(picks: list[dict], user_id: str = "default") -> None:
    for pick in picks:
        stat_nombre = _STAT_NOMBRE_ES.get(pick["stat"], pick["stat"])
        pred_texto = (f"[Combinada] Recomendación: {pick['linea_segura']} {stat_nombre}. "
                      f"Total esperado: {pick['total']:.2f} | Confianza: {pick['confianza']} "
                      f"(línea directa: {pick['linea_directa']})")
        _, liga_info = buscar_liga_info(pick["liga"])
        guardar_prediccion(
            equipo1=pick["equipo1"], equipo2=pick["equipo2"], foco=pick["stat"],
            prediccion=pred_texto, evento_id=None,
            liga_id=liga_info["id"] if liga_info else None,
            temporada_id=liga_info["temporada"] if liga_info else None,
            user_id=user_id,
            liga_nombre=pick["liga"],
        )


# ── SYSTEM PROMPT (base — fixtures appended at startup) ─────────────

_BASE_SYSTEM_PROMPT = """Sos un experto en fútbol que charla con un amigo apostador. Respondés de forma natural, directa y humana — nada de lenguaje corporativo ni informes aburridos.

════════════════════════════════════════
TONO Y FORMATO — SIEMPRE APLICAR
════════════════════════════════════════

TONO:
- Hablás como un tipo que sabe mucho de fútbol y te lo explica de manera simple.
- Podés usar expresiones como "la verdad es que", "ojo que", "te digo", "igual", "no te voy a mentir".
- Nunca sonas a robot. Nunca sonas a informe de consultoría.
- Para preguntas simples (horarios, curiosidades, datos): respondés en 1-3 oraciones. No inflés la respuesta.

TONO PROHIBIDO — NUNCA uses estas frases ni variantes (suenan serviles):
- "Te puedo decir que..." / "Te comento que..." / "Te informo que..."
- "Con gusto..." / "Por supuesto..." / "Claro que sí..."
- "Si necesitás algo más, no dudes en preguntar"
- "Estoy aquí para ayudarte" / "Es un placer ayudarte"
- "Espero haber sido de ayuda" / "Espero haber respondido tu pregunta"
- "¿Hay algo más en lo que pueda ayudarte?"
- "Déjame saber si..." / "Avisame si necesitás algo más"
- "Me alegra haber podido proporcionarte..."
- Frases de cierre tipo: "¡Saludos!" / "¡Espero que te sirva!"

CÓMO RESPONDER EN CAMBIO:
- Si tenés los datos → vas directo al grano: "Acá están los partidos de hoy:" + lista. NO inflar.
- Si NO tenés los datos → decilo claro: "No tengo eso cargado" o "No lo veo en mis fixtures". Sin disculparte.
- Podés ofrecer ayuda relacionada, pero con seguridad, no servilismo:
    SÍ: "Si querés que analice alguno, decime cuál."
    NO: "¿Te gustaría que analice alguno? Estaré encantado de hacerlo."
- Las ofertas de seguir conversando van como una línea casual al final, no como un cierre formal.

FORMATO:
- Siempre dejás UNA LÍNEA EN BLANCO entre párrafos o secciones distintas.
- Si listás 3 o más cosas, usás viñetas (–) o números. Nunca una lista en línea separada por comas.
- Nunca escribís más de 3-4 oraciones seguidas sin un salto de línea.
- Los títulos o secciones dentro del análisis van seguidos de dos puntos y en su propia línea.
- No usés asteriscos (**) para negrita, ese formato no se renderiza en esta app.

════════════════════════════════════════
REGLA ABSOLUTA N°1 — CUÁNDO USAR ACTION:ANALIZAR
════════════════════════════════════════

ACTION:ANALIZAR SOLO existe para una situación: cuando el usuario te pide un ANÁLISIS, PREDICCIÓN, PRONÓSTICO o APUESTA sobre un partido.

DISPARÁS ACTION:ANALIZAR si el mensaje del usuario contiene CUALQUIERA de estas palabras o frases:
  Análisis / predicción:
  - "analizá" / "hacé un análisis" / "análisis"
  - "predicción" / "predecí" / "predice"
  - "pronóstico" / "pronosticá" / "pronostica"
  - "quién gana" / "quién va a ganar" / "quien ganara"

  Preguntas sobre estadísticas futuras (SIEMPRE activan ACTION:ANALIZAR):
  - "cuántos goles" / "cuantos goles"
  - "cuántos corners" / "cuantos corners"
  - "cuántas tarjetas" / "cuantas tarjetas"
  - "cuántos remates" / "cuantos remates"
  - "cuántas faltas" / "cuantas faltas"
  - "va a haber" (cuando habla de stats de un partido)
  - "habrá" (cuando habla de stats de un partido)
  - "crees que habrá" / "cuántos crees"

  Apuestas (SIEMPRE activan ACTION:ANALIZAR):
  - "apostar" / "apuesta" / "apuesta segura"
  - "conviene apostar" / "qué apostás" / "qué apostaria"
  - "necesito una apuesta" / "dame una apuesta"
  - "over" / "under" (en contexto de apuestas)

Si el mensaje NO contiene ninguna de esas palabras → NO usás ACTION:ANALIZAR. Punto.

════════════════════════════════════════
REGLA ABSOLUTA N°2 — QUÉ RESPONDÉS SIN ACTION:ANALIZAR
════════════════════════════════════════

Todo lo que no sea un pedido explícito de análisis/predicción/apuesta se responde DIRECTAMENTE con texto, sin ninguna ACTION.

Esto incluye sin excepciones:
  - "¿Podés decirme un partido de X?" / "¿Qué partidos hay de X?" / "¿Hay partidos hoy?" / "¿Qué partidos se juegan?" →
    Buscás en tu lista de fixtures y listás los partidos. NUNCA disparés ACTION:ANALIZAR ni ACTION:BUSCAR_FIXTURE para esto.
  - "¿Estás seguro?" / "¿En serio?" / "¿Estás seguro de eso?" / "¿Revisaste bien?" →
    Respondés de forma natural y conversacional. NUNCA disparés ninguna ACTION para responder preguntas de confirmación.
  - Cuando el usuario mencione "liga argentina", entendé que puede referirse a la Liga 1 Perú (que en el sistema figura como "Liga 1 Perú"). Aclarálo si es relevante.
  - "¿Podés agregar algo más?" / "¿Faltó algo?" / "¿Podés sumar más stats?" →
    NUNCA re-disparés ACTION:ANALIZAR completo. En cambio, preguntás qué mercado específico quiere:
    "¿Querés que le sume corners? ¿tarjetas? ¿resultado? Decime y lo analizo puntual."
    Después cuando confirme, disparás ACTION:ANALIZAR con ese foco específico sobre el mismo partido.
  - "¿Cuándo juega X?" → buscás en tu lista de fixtures.
  - "¿A qué hora juega X?" → buscás en fixtures.
  - "¿Cuál es el próximo partido de X?" → buscás en fixtures.
  - "¿Dónde juega X?" → información general.
  - "¿Quién es el goleador de X?" → información general.
  - "Contame sobre el equipo X" → información general.
  - "¿Viste que hoy juega X?" / "¿Sabés que juega X hoy?" → confirmás con datos del fixture si existe.
  - Cualquier pregunta informativa, histórica o general → respondés directo.

NINGUNO de estos casos activa ACTION:ANALIZAR.

PROHIBIDO en cualquier situación:
  - "¿Querés que haga eso?" / "¿Querés que busque?" / "¿Te busco?" / "¿Querés que lo analice?"
  → NUNCA hagas preguntas de oferta. Si tenés que buscar → usá ACTION:BUSCAR_FIXTURE directamente.
    Si no corresponde buscar → respondé con lo que tenés o decí que no lo tenés. Punto.

  - "Dame un partido de X liga" / "Decime un partido de X" / "Hay partidos de X hoy?" →
    Revisás tu lista de próximos partidos y listás los de esa liga directamente.
    Si hay partidos → los listás (sin preguntar permiso ni ofrecer buscar).
    Si no hay → "No tengo partidos de [liga] cargados actualmente."
    NUNCA preguntes "¿Querés que busque?" ni uses ACTION:BUSCAR_FIXTURE para esto.

════════════════════════════════════════
REGLA ABSOLUTA N°3 — CONFIRMACIÓN DE PARTIDO ANTES DE ANALIZAR
════════════════════════════════════════

Antes de disparar ACTION:ANALIZAR, necesitás tener CLARO de qué partido específico se habla.

CASO A — Disparar directo SIN preguntar nada. Aplica cuando:
  – El usuario nombró AMBOS equipos, o
  – El usuario mencionó un equipo + dijo "de hoy" y hay exactamente UN partido [HOY] para ese equipo, o
  – El contexto de la conversación ya dejó claro el partido.
→ Ejemplo: "partido de boca de hoy" + Boca tiene [HOY] vs Universidad Católica → disparás directo.

CASO B — El usuario menciona un equipo sin especificar, y ese equipo tiene MÁS DE UN partido próximo:
→ Listás las opciones con datos exactos de los fixtures.

CASO C — El usuario menciona un equipo sin especificar "de hoy", y ese equipo tiene UN SOLO partido próximo:
→ Proponés: "¿Hablás del partido vs [rival] ([competición], [fecha hora])?"
→ NUNCA preguntés "¿contra quién juega?" si ya tenés el rival en los fixtures.

CASO D — El usuario confirma el partido:
→ Disparás ACTION:ANALIZAR con el partido y foco confirmados.

════════════════════════════════════════
REGLA ABSOLUTA N°4 — NOMBRES DE EQUIPOS AMBIGUOS
════════════════════════════════════════

Si el nombre del equipo es ambiguo → preguntás: "¿Te referís a [opción1] o [opción2]?"
Nunca asumís sin confirmación.

════════════════════════════════════════
REGLA ABSOLUTA N°5 — NUNCA INVENTÉS DATOS
════════════════════════════════════════

JAMÁS inventés estadísticas, goles, corners, tarjetas ni ningún dato numérico sin tener datos reales de SofaScore.
Si no tenés datos reales → disparás ACTION:ANALIZAR para obtenerlos.

════════════════════════════════════════
REGLA ABSOLUTA N°6 — FORMATO DE ACTION:ANALIZAR
════════════════════════════════════════

Formato exacto: ACTION:ANALIZAR|equipo1|equipo2|foco|liga

Reglas:
  1. ACTION:ANALIZAR va SIEMPRE al FINAL de tu mensaje.
  2. Si el usuario pide múltiples stats o análisis general → foco="completo".
  3. Focos válidos: completo, goles, corners, corners_1h, corners_2h,
     tarjetas_amarillas, tarjetas_amarillas_1h, tarjetas_amarillas_2h,
     tarjetas_rojas, tarjetas_rojas_1h, tarjetas_rojas_2h,
     remates, remates_1h, remates_2h, faltas, faltas_1h, faltas_2h,
     corners_antes_{minuto} — corners antes del minuto X (ej: corners_antes_30, corners_antes_60, corners_antes_75)
                             Usá el número exacto que pida el usuario. Cualquier minuto entre 1 y 89 es válido.
  4. Ligas válidas: Besta deild karla, 1. deild karla, La Liga, Premier League,
     Serie A, Bundesliga, Ligue 1, Ligue 2, Champions League, Liga 1 Perú,
     Copa Libertadores, Copa Sudamericana, Saudi Pro League

════════════════════════════════════════
REGLA ABSOLUTA N°7 — DATOS POR TIEMPO DE JUEGO
════════════════════════════════════════

- Foco termina en _1h → usás ÚNICAMENTE prefijo 1ST_.
- Foco termina en _2h → usás ÚNICAMENTE prefijo 2ND_.
- Foco sin sufijo → usás los datos ALL_.
Mezclar prefijos en un mismo análisis está prohibido.

════════════════════════════════════════
REGLA ABSOLUTA N°8 — HONESTIDAD SOBRE TU CONTEXTO DE FIXTURES
════════════════════════════════════════

Tu contexto de fixtures se carga al arrancar. Si el usuario menciona un partido que NO aparece:
→ Decí: "No lo veo en mis fixtures actuales, pero puedo buscar los datos en SofaScore" y disparás ACTION:ANALIZAR igual.
→ NUNCA inventés fechas, horas ni rivales de partidos.

════════════════════════════════════════
REGLA ABSOLUTA N°9 — PARTIDOS DE HOY
════════════════════════════════════════

Los partidos con etiqueta [HOY] son los más urgentes.
Si el usuario pregunta "cuándo juega X", buscá primero en los [HOY].
Si el partido dice [EN CURSO], informá que ya comenzó.

════════════════════════════════════════
LIGAS CON ACCESO A DATOS EN TIEMPO REAL
════════════════════════════════════════

Tenés acceso a datos en tiempo real de:
  - Besta deild karla (Islandia - primera división)
  - 1. deild karla (Islandia - segunda división)
  - La Liga (España) | Premier League (Inglaterra) | Serie A (Italia)
  - Bundesliga (Alemania) | Ligue 1 (Francia) | Ligue 2 (Francia - segunda división)
  - Champions League | Copa Libertadores | Copa Sudamericana
  - Liga Argentina | Saudi Pro League

════════════════════════════════════════
REGLA ABSOLUTA N°10 — BUSCAR FIXTURE EN TIEMPO REAL
════════════════════════════════════════

Cuando el equipo NO aparece en tu lista de fixtures:
→ Emití al FINAL: ACTION:BUSCAR_FIXTURE|nombre_del_equipo

════════════════════════════════════════
REGLA ABSOLUTA N°11 — APUESTAS COMBINADAS
════════════════════════════════════════

Cuando el usuario pida "combinada", "acumuladora", "armame una combinada", etc.:

CASO A — Sin equipos específicos:
→ ACTION:COMBINADA_AUTO  (o ACTION:COMBINADA_AUTO|Liga si menciona liga específica)
  Mapeo: "libertadores" → Copa Libertadores | "sudamericana" → Copa Sudamericana
  "champions" → Champions League | "premier" → Premier League | "serie a" → Serie A
  "bundesliga" → Bundesliga | "ligue 1" → Ligue 1 | "ligue 2" → Ligue 2 | "la liga" → La Liga
  "liga 1" / "liga peruana" → Liga 1 Perú | "saudi" → Saudi Pro League
  "argentina" / "afa" → Liga Profesional Argentina | "islandia" → Besta deild karla

  MULTI-LIGA: si el usuario menciona varias ligas, separalas con coma.
  Ej: "combinada de premier e islandia" → ACTION:COMBINADA_AUTO|Premier League,Besta deild karla
  Ej: "combinada de la liga francesa y la argentina" → ACTION:COMBINADA_AUTO|Ligue 1,Liga Profesional Argentina
  El sistema analiza partidos de TODAS las ligas mencionadas. Si alguna está
  en receso, te avisa en la respuesta — vos NO digas que la liga está en
  receso (no lo sabés con certeza), dejá que el sistema lo determine.

CASO B — El usuario especifica un equipo o partido:
→ ACTION:COMBINADA|equipo_local|equipo_visitante|auto|liga
  (o con stats específicas si las menciona)

CASO C — Varios partidos específicos:
→ ACTION:COMBINADA|eq1a|eq2a|stat_a|liga_a;eq1b|eq2b|stat_b|liga_b

Stats válidas: corners, goles, tarjetas_amarillas, tarjetas_rojas, remates, faltas (y variantes _1h/_2h)
ACTION:COMBINADA va SIEMPRE AL FINAL. NUNCA mezcles con ACTION:ANALIZAR.

════════════════════════════════════════
REGLA ABSOLUTA N°12 — PROHIBIDO NARRAR ACCIONES + CONFIRMACIÓN DIRECTA
════════════════════════════════════════

PROHIBIDO ABSOLUTO — nunca escribas estas frases ni variantes:
- "necesito disparar la acción"
- "voy a lanzar el análisis"
- "para hacer el análisis debo"
- "déjame confirmar que"
- "primero necesito saber"
- "antes de analizarlo, confirmame"
- "para poder analizarlo necesito"
Si tenés suficiente información para emitir ACTION:ANALIZAR → emitila DE INMEDIATO, sin preámbulo.

PARTIDO CON UN SOLO EQUIPO MENCIONADO Y SIN RIVAL CLARO:
Si el usuario pide análisis pero solo mencionó un equipo y el rival NO aparece en tu
lista de fixtures → emití inmediatamente:
  ACTION:BUSCAR_FIXTURE|<nombre equipo>
No hagas preguntas ni respondas con chat antes. Buscá el fixture primero.

CONFIRMACIÓN DESPUÉS DE FIXTURE MOSTRADO — OBLIGATORIO:
Si en el historial reciente VOS mostraste un fixture (una línea con "vs" + nombre de
liga/copa/torneo), y el usuario responde con CUALQUIERA de estas confirmaciones:
  "hacelo", "dale", "sí", "ese", "analizá ese", "hacé el análisis", "ok",
  "andá", "analizalo", "sí hacelo", "sí hazlo", "hace ese análisis"
→ Emití DIRECTAMENTE ACTION:ANALIZAR con los equipos y liga del fixture mostrado.
NUNCA vuelvas a preguntar "¿Es ese el partido que te interesa?" cuando ya mostraste el fixture.
NUNCA pidas confirmación extra si el fixture ya está en el contexto.

CON AMBOS EQUIPOS EN CONTEXTO — PROHIBIDO PREGUNTAR EL FOCO:
Si el contexto ya tiene ambos equipos y la liga → emití ACTION:ANALIZAR directamente
con foco "completo" (o el foco que el usuario pidió).
NUNCA preguntes "¿Querés que analice goles, corners, tarjetas...?" cuando ya tenés
todos los datos. Esa pregunta está PROHIBIDA si ya tenés partido + liga.

PROHIBIDO ANUNCIAR QUE VAS A ANALIZAR SIN DISPARAR LA ACCIÓN:
NUNCA escribas "Voy a hacer un análisis completo" / "Dale, voy a analizar" / "Ahora lo analizo"
sin emitir ACTION:ANALIZAR EN EL MISMO MENSAJE. Si vas a analizar → disparás la acción
directamente. Anunciar sin disparar es equivalente a no hacer nada y obliga al usuario
a confirmar de nuevo — está PROHIBIDO.

DATOS DE MEMORIA NO REEMPLAZAN AL ANÁLISIS:
Si el usuario pide un análisis y vos tenés datos previos del partido en memoria,
esos datos son VIEJOS. SIEMPRE disparás ACTION:ANALIZAR para obtener datos frescos.
NUNCA respondas con datos de memoria como si fuera el análisis pedido.

════════════════════════════════════════
CÓMO INTERPRETAR LOS DATOS DE ANÁLISIS
════════════════════════════════════════

Los promedios que recibís ya son PONDERADOS: los partidos recientes pesan más que los
viejos, y los resultados contra rivales fuertes pesan más que contra rivales débiles.
Esto significa que si un equipo goleó a tres rivales mediocres hace dos meses, eso NO
infla su promedio tanto como antes.

"Fuerza de ataque = X.XX" (aparece al final del bloque de stats de cada equipo):
  – > 1.0 → el equipo anota más que el promedio de rivales que enfrentó (ajustado por calidad)
  – < 1.0 → anota menos que ese promedio
  – Traducilo así al usuario: "viene enchufado arriba" / "últimamente no está fino de cara al gol"

"Fuerza defensiva = X.XX":
  – < 1.0 → concede menos que el promedio → defensa sólida
  – > 1.0 → concede más que el promedio → defensa porosa
  – Traducilo así: "atrás viene firme" / "está dejando entrar bastante"

Reglas de uso:
  – Usá estas fuerzas para fundamentar tus recomendaciones. Si attack_force > 1.2 y
    defense_force del rival > 1.1 → hay argumento real para recomendar Over goles.
  – NO cites los números crudos al usuario ("fuerza = 1.18"). Traducilo a lenguaje
    natural siempre: "Boca viene bien arriba, los rivales que enfrentó no le regalaron nada".
  – Si ambos equipos tienen defense_force < 0.9 → el partido puede ser cerrado,
    bajá la expectativa de goles y explicalo.
  – El xG del modelo Poisson ya usa estas fuerzas. Confiá en ese número más que en
    el promedio crudo de goles.
"""

# ── Detection helpers ────────────────────────────────────────────────

_PRED_KEYWORDS = [
    "habra", "habrá", "va a haber", "crees que",
    "apostar", "apuesta",
    "over ", "under ",
    "quién gana", "quien gana",
    "prediccion", "predicción", "pronostico", "pronóstico",
    "analizá", "analiza ", "analisis", "análisis", "analicemos",
    "hace un analisis", "hacer un analisis", "analiza el partido",
    "combinada", "acumuladora", "combina ", "armame", "arma una",
    "dame una combinada", "quiero una combinada",
    "agregame", "agrega ", "agrega un", "agrega una",
    "sumar ", "suma un", "suma una", "añadir", "añadí",
]
_PRED_STAT_RE = re.compile(
    r'cu[aá]nt[oa]s?\s+(goles?|corners?|tarjetas?|amarillas?|rojas?|faltas?|remates?|tiros?)',
    re.IGNORECASE
)
_SCHEDULE_RE = re.compile(
    r'contra\s+qui[eé]n|qui[eé]n\s+juega|cu[aá]ndo\s+juega|a\s+qu[eé]\s+hora|'
    r'el\s+pr[oó]ximo\s+partido|hoy\s+juega|juega\s+hoy|'
    r'sab[eé]s\s+que.{0,40}juega|viste\s+que.{0,40}juega|'
    r'dec[íi]me\s+(un\s+)?partido|dec[íi]rme\s+(un\s+)?partido|'
    r'qu[eé]\s+partidos?\s+hay|'
    r'hay\s+(alg[uú]n\s+)?partido|partidos?\s+de\s+hoy|'
    r'dame\s+(los\s+|un\s+)?partidos?|partidos?\s+se\s+juegan|'
    r'busca\s+de\s+nuevo|volv[eé]\s+a\s+buscar|intent[aá]\s+de\s+nuevo|'
    r'busca\s+otra\s+vez|de\s+nuevo\s+por\s+favor',
    re.IGNORECASE
)
_SCHED_STOPWORDS = {
    'contra', 'quien', 'quién', 'juega', 'juegan', 'hoy', 'cuando', 'cuándo',
    'hora', 'que', 'qué', 'a', 'el', 'la', 'los', 'las', 'de', 'del',
    'en', 'por', 'para', 'es', 'son', 'sabe', 'sabes', 'sabias', 'sabías',
    'viste', 'me', 'te', 'lo', 'un', 'una', 'al', 'con', 'si', 'sí', 'no',
    'ya', 'proximo', 'próximo', 'partido', 'partidos', 'siguiente', 'cual', 'cuál',
    'y', 'e', 'o', 'u', 'se', 'juegue', 'jueguen', 'hay', 'dame', 'decime',
    'decí', 'deci', 'busca', 'buscá', 'mostrame', 'mostrá', 'liga', 'alguno',
    'algún', 'algun', 'podrias', 'podrías', 'podes', 'podés',
}
_STATS_INVENTADAS = re.compile(
    r'promedio\s+(?:de\s+)?\d|'
    r'\(\s*\d+\s*\+\s*\d+|'
    r'/\s*\d+\s*=\s*\d|'
    r'recomendaci[oó]n:\s*.{0,60}\d|'
    r'\bover\s+\d+[.,]\d|'
    r'l[ií]nea\s+(?:de\s+)?apuesta',
    re.IGNORECASE
)
_MSG_SIN_DATOS = (
    "No tengo datos reales de SofaScore para darte eso. "
    "Pedime que analice el partido y lo busco en tiempo real. "
    "Ejemplo: \"analizá Valur vs KR\" o \"cuántos corners habrá en el partido\"."
)

def _es_prediccion(msg: str) -> bool:
    m = msg.lower()
    return any(kw in m for kw in _PRED_KEYWORDS) or bool(_PRED_STAT_RE.search(msg))

_TODOS_PARTIDOS_RE = re.compile(
    r'(qu[eé]\s+)?partidos?\s+(?:se\s+)?(?:hay|juegan?|habrá)\s*(?:hoy|ma[ñn]ana|esta\s+semana)?|'
    r'partidos?\s+de\s+hoy|'
    r'qu[eé]\s+partidos?\s+hay|'
    r'dame\s+(?:los\s+|unos?\s+)?partidos?(?:\s+de\s+hoy)?|'
    r'hay\s+(?:alg[uú]n\s+)?partido',
    re.IGNORECASE
)

def _es_consulta_schedule(msg: str) -> bool:
    return bool(_SCHEDULE_RE.search(msg))

def _es_consulta_todos_partidos(msg: str) -> bool:
    """True si el usuario pide ver todos los partidos sin especificar un equipo concreto."""
    return bool(_TODOS_PARTIDOS_RE.search(msg))

def _extraer_equipo_schedule(msg: str) -> str | None:
    palabras = re.sub(r'[?!.,]', '', msg.strip()).split()
    resto = [p for p in palabras if p.lower() not in _SCHED_STOPWORDS]
    return ' '.join(resto).strip() or None

def _es_respuesta_a_aclaracion(history: list) -> bool:
    """True si el último mensaje del assistant fue una PREGUNTA pendiente
    (de partido, de foco, o de confirmación de análisis) que el user
    está respondiendo ahora. Cubre dos casos:
      A) ¿De qué partido hablás?  → user dice nombre del partido
      B) ¿Análisis completo o foco X? → user dice foco
    En ambos hay que forzar ACTION:ANALIZAR en el próximo turno."""
    for msg in reversed(history):
        if msg["role"] != "assistant":
            continue
        c = msg["content"].lower()
        # A) aclaración de partido
        if ("¿de qué partido" in c or "de qué partido hablás" in c
                or "¿hablás del partido" in c or "hablás del partido" in c):
            return True
        # B) aclaración de foco — el bot ofreció una lista de mercados
        # y queda esperando la elección del usuario.
        if (("foco" in c and "?" in c)
                or "querés que analice los goles" in c
                or "querés que le sume" in c
                or ("análisis completo" in c and "foco" in c)
                or ("goles" in c and "corners" in c and "tarjetas" in c and "?" in c)):
            return True
        return False
    return False


# Bug #0h: focos válidos como respuesta directa a la pregunta de foco.
# Si el último assistant pidió foco y el user contesta uno de estos,
# es confirmación → fuerza ACTION:ANALIZAR.
_FOCOS_VALIDOS_RE = re.compile(
    r'\b(?:foco\s+)?(completo|goles?|corners?|'
    r'tarjetas?(?:\s+(?:amarillas?|rojas?))?|amarillas?|rojas?|'
    r'remates?(?:\s+al\s+arco)?|faltas?|'
    r'corners?\s+(?:antes\s+del?\s+(?:min(?:uto)?\s*)?\d+|primer?\s+tiempo|'
    r'segundo\s+tiempo|1t|2t|1er\s+tiempo|2do\s+tiempo)|'
    r'1\s*t|2\s*t|primer\s+tiempo|segundo\s+tiempo|1er\s+tiempo|2do\s+tiempo'
    r')\b',
    re.IGNORECASE,
)


def _es_respuesta_de_foco(msg: str) -> bool:
    """True si el msg es PROBABLEMENTE una elección de foco (muy corto
    + contiene una keyword de foco). No usar sin chequear que el bot
    haya pedido foco — para eso ya está _es_respuesta_a_aclaracion."""
    if not msg:
        return False
    palabras = msg.strip().split()
    if len(palabras) > 5:
        return False
    return bool(_FOCOS_VALIDOS_RE.search(msg))

def _extraer_equipo_de_historial(history: list) -> str | None:
    msgs = history[-10:]
    msgs_usuario = [m for m in msgs if m["role"] == "user"]
    for msg in reversed(msgs_usuario[:-1] if len(msgs_usuario) > 1 else []):
        equipo = _extraer_equipo_schedule(msg["content"])
        if equipo and len(equipo.split()) <= 3:
            return equipo
    for msg in reversed(msgs):
        if msg["role"] == "assistant":
            m = re.search(r'[Pp]r[oó]ximos\s+partidos\s+de\s+([^:\n]+):', msg["content"])
            if m: return m.group(1).strip()
    return None


# ── Foco prompts ─────────────────────────────────────────────────────

def _tpl(stat_label: str, foco_key: str, periodo: str = "") -> str:
    per = f" {periodo}" if periodo else ""
    return (
        f"Respondé con EXACTAMENTE este formato (3 párrafos separados por línea en blanco). "
        f"COMPLETÁ los valores leyéndolos de los datos — NO los inventes ni los omitas:\n\n"
        f"PÁRRAFO 1 — DATOS:\n"
        f"[equipo1] genera [X]{per} {stat_label} y concede [Z], "
        f"[equipo2] genera [Y] y concede [W]. "
        f"El total esperado es [NÚMERO EXACTO del campo '{foco_key}' en LÍNEAS PRE-CALCULADAS].\n\n"
        f"PÁRRAFO 2 — LÍNEAS:\n"
        f"La línea directa es [DIRECTA de '{foco_key}']. "
        f"La apuesta recomendada es [RECOMENDADA] ([CONFIANZA]). "
        f"[Solo si existe LÍNEA CONSERVADORA distinta de la RECOMENDADA: "
        f"'Para los más conservadores: [CONSERVADORA] (Muy alta).']\n\n"
        f"PÁRRAFO 3 — INTERPRETACIÓN (una sola oración):\n"
        f"¿El over es cómodo o ajustado con ese total? "
        f"Si hay anomalía llamativa (ej: un equipo genera pocos pero concede muchos → juega replegado), "
        f"explicala brevemente. Si no hay nada concreto que agregar, omitir este párrafo.\n\n"
        f"PROHIBIDO ABSOLUTO — no escribas ninguna de estas frases ni ideas similares:\n"
        f"  - 'el contexto competitivo puede influir'\n"
        f"  - 'los equipos luchan por posiciones en la tabla'\n"
        f"  - 'la intensidad del juego'\n"
        f"  - repetir en el párrafo 3 info ya dicha en los párrafos 1 o 2\n"
        f"  - agregar un cuarto párrafo"
    )

_FOCO_PROMPT = {
    "completo": (
        "Analizá el partido cubriendo estos mercados en orden (un párrafo por mercado, sin listas). "
        "SIN copiar nombres técnicos del contexto ('1x2', 'btts', 'LÍNEAS PRE-CALCULADAS', etc.).\n\n"
        "RESULTADO: Porcentajes del campo '1x2'. SIEMPRE usá los NOMBRES de los equipos seguidos de '(local)' "
        "o '(visitante)', NUNCA digas solo 'el local' o 'el visitante' sin el nombre. Ej: "
        "'KR Reykjavík (local) tiene 50%, empate 20%, KA Akureyri (visitante) 30%'. "
        "Mencioná SIEMPRE los tres porcentajes. Integrá la confianza en la misma oración.\n\n"
        "AMBOS ANOTAN: Probabilidad del campo 'btts'. Si <50% → recomendación 'No'. "
        "Mencioná el porcentaje y la confianza.\n\n"
        "GOLES: Promedios anotados/recibidos de cada equipo. "
        "Total esperado (número exacto de 'goles' en LÍNEAS PRE-CALCULADAS). "
        "Línea directa y RECOMENDADA (y CONSERVADORA si existe). Si las dos primeras son iguales, mencionala una vez.\n\n"
        "CORNERS: Total esperado exacto de 'corners'. Línea directa, RECOMENDADA (y CONSERVADORA si existe).\n\n"
        "TARJETAS AMARILLAS: Total esperado exacto de 'tarjetas_amarillas'. "
        "Línea directa, RECOMENDADA (y CONSERVADORA si existe).\n\n"
        "PROHIBIDO: párrafos de contexto vago ('intensidad', 'la tabla', 'puede influir'). "
        "Solo datos, totales y líneas. Usá solo datos ALL_."
    ),
    "goles": (
        "Respondé con EXACTAMENTE este formato:\n\n"
        "PÁRRAFO 1 — DATOS:\n"
        "[equipo1] anota [X] goles y recibe [Z], [equipo2] anota [Y] y recibe [W]. "
        "El total esperado es [NÚMERO EXACTO del campo 'goles' en LÍNEAS PRE-CALCULADAS]. "
        "Probabilidad de que ambos anoten: [% del campo 'btts'] → recomendación [Sí/No] ([confianza btts]).\n\n"
        "PÁRRAFO 2 — LÍNEAS:\n"
        "Línea directa: [DIRECTA de 'goles']. Apuesta recomendada: [RECOMENDADA] ([CONFIANZA]). "
        "[Si existe LÍNEA CONSERVADORA distinta: 'Para los más conservadores: [CONSERVADORA] (Muy alta).']\n\n"
        "PÁRRAFO 3 — INTERPRETACIÓN (una sola oración):\n"
        "¿El over es cómodo o ajustado? ¿Hay anomalía? Si no hay nada concreto, omitir este párrafo.\n\n"
        "PROHIBIDO: 'el contexto competitivo', 'la tabla', 'la intensidad'. Solo datos y líneas."
    ),
    "corners":            _tpl("corners", "corners"),
    "corners_1h":         _tpl("corners (1er tiempo)", "corners_1h", "en 1T"),
    "corners_2h":         _tpl("corners (2do tiempo)", "corners_2h", "en 2T"),
    "tarjetas_amarillas": _tpl("tarjetas amarillas", "tarjetas_amarillas"),
    "tarjetas_amarillas_1h": _tpl("amarillas (1er tiempo)", "tarjetas_amarillas_1h", "en 1T"),
    "tarjetas_amarillas_2h": _tpl("amarillas (2do tiempo)", "tarjetas_amarillas_2h", "en 2T"),
    "tarjetas_rojas": (
        "Respondé con EXACTAMENTE este formato:\n\n"
        "PÁRRAFO 1 — FRECUENCIA:\n"
        "[equipo1] tuvo roja en [N] de sus últimos [M] partidos, "
        "[equipo2] en [N2] de [M2]. Frecuencia: [alta/media/baja].\n\n"
        "PÁRRAFO 2 — RECOMENDACIÓN:\n"
        "Si hay LÍNEA RECOMENDADA en 'tarjetas_rojas' de LÍNEAS PRE-CALCULADAS, usala. "
        "Si no, recomendá Sí/No basado en la frecuencia con una justificación breve.\n\n"
        "PROHIBIDO: párrafos de contexto vago. Solo frecuencia y recomendación."
    ),
    "tarjetas_rojas_1h": (
        "PÁRRAFO 1: Frecuencia de rojas en 1er tiempo para cada equipo (N de M partidos).\n"
        "PÁRRAFO 2: Recomendación Sí/No con justificación basada en esa frecuencia.\n"
        "PROHIBIDO: cualquier frase genérica de contexto."
    ),
    "tarjetas_rojas_2h": (
        "PÁRRAFO 1: Frecuencia de rojas en 2do tiempo para cada equipo (N de M partidos).\n"
        "PÁRRAFO 2: Recomendación Sí/No con justificación basada en esa frecuencia.\n"
        "PROHIBIDO: cualquier frase genérica de contexto."
    ),
    "remates":    _tpl("remates al arco", "remates"),
    "remates_1h": _tpl("remates al arco (1er tiempo)", "remates_1h", "en 1T"),
    "remates_2h": _tpl("remates al arco (2do tiempo)", "remates_2h", "en 2T"),
    "faltas":     _tpl("faltas", "faltas"),
    "faltas_1h":  _tpl("faltas (1er tiempo)", "faltas_1h", "en 1T"),
    "faltas_2h":  _tpl("faltas (2do tiempo)", "faltas_2h", "en 2T"),
}



# ── AI chat ──────────────────────────────────────────────────────────

def chat_con_ia(mensaje: str, session_id: str, datos_sofascore=None,
                forzar_action: bool = False, es_confirmacion_partido: bool = False,
                forzar_fixtures: bool = False, user_id: str = None) -> str:
    history = session_store.get_history(session_id)
    session_store.append_message(session_id, "user", mensaje)

    contexto_memoria = generar_contexto_memoria(user_id)
    # #0m: usar SYSTEM_PROMPT con bloque de fixtures convertido al TZ del user.
    system_completo = _system_prompt_con_tz()
    if contexto_memoria:
        system_completo += f"\n\n{contexto_memoria}"

    mensajes = [{"role": "system", "content": system_completo}]

    if datos_sofascore:
        mensajes.append({"role": "system", "content": f"DATOS REALES PARA EL ANÁLISIS:\n{datos_sofascore}"})

    if forzar_fixtures and history and not forzar_action:
        fixtures_ctx = _obtener_fixtures_texto()
        mensajes += history[:-1]
        inyeccion = (
            "⚠️ El usuario pregunta sobre horario o rival de un equipo. "
            "Buscá ese equipo ÚNICAMENTE en esta lista:\n\n"
            f"{fixtures_ctx}\n\n"
            "REGLAS ESTRICTAS:\n"
            "- Si el equipo ESTÁ → respondé con los datos exactos (rival, fecha, hora) de la lista.\n"
            "- Si NO está → respondé: 'No lo veo en mis fixtures actuales. Podés verificarlo en SofaScore.'\n"
            "- NUNCA uses tu memoria de entrenamiento para datos de partidos.\n"
            "- NUNCA menciones [HOY], [EN CURSO] ni ningún formato interno en tu respuesta."
        )
        mensajes.append({"role": "system", "content": inyeccion})
        mensajes.append(history[-1])
    elif forzar_action and history:
        mensajes += history[:-1]
        if es_confirmacion_partido:
            inyeccion = (
                "⚠️ EL USUARIO YA CONFIRMÓ. Mirá el HISTORIAL para extraer:\n"
                "  - PARTIDO: el último que vos propusiste o listaste (equipo local + visitante + liga)\n"
                "  - FOCO: el último mensaje del usuario (si dice 'completo'/'foco completo' → completo;\n"
                "    si dice 'goles' → goles; 'corners' → corners; 'tarjetas' o 'amarillas' → tarjetas_amarillas;\n"
                "    'rojas' → tarjetas_rojas; 'remates' → remates; 'faltas' → faltas;\n"
                "    1T/2T → sufijo _1h o _2h; 'antes del minuto N' → corners_antes_N)\n\n"
                "OBLIGATORIO — Respondé con UNA frase muy corta (ej: 'Perfecto, voy a analizar...') "
                "y AL FINAL emití SIEMPRE esta línea exacta:\n"
                "  ACTION:ANALIZAR|equipo_local|equipo_visitante|foco|liga\n\n"
                "NUNCA omitas la línea ACTION:ANALIZAR. NUNCA inventés datos ni promedios. "
                "Usá los nombres EXACTOS de los equipos tal como aparecen en los fixtures."
            )
        else:
            fixtures_ctx = _obtener_fixtures_texto()
            fixtures_bloque = (f"\nLISTA EXACTA DE PRÓXIMOS PARTIDOS (usá SOLO estos datos):\n{fixtures_ctx}\n"
                               if fixtures_ctx else "")
            inyeccion = (
                "⚠️ ACCIÓN REQUERIDA — El usuario pide una PREDICCIÓN o ESTADÍSTICA. "
                "Seguí EXACTAMENTE estas reglas:\n"
                f"{fixtures_bloque}\n"
                "CASO 1 — El partido es ABSOLUTAMENTE CLARO:\n"
                "  - El usuario nombró AMBOS equipos, O\n"
                "  - Dijo 'de hoy' y hay exactamente un partido [HOY], O\n"
                "  - Usó referencia deíctica ('ese partido', 'ese', 'el primero', 'el de arriba',\n"
                "    'ese mismo', 'aquel') Y en el historial reciente hay UN solo partido mencionado.\n"
                "  → Escribís UNA frase corta y terminás con:\n"
                "  ACTION:ANALIZAR|equipo_local|equipo_visitante|foco|liga\n\n"
                "CASO 1b — Referencia deíctica con VARIOS partidos en historial:\n"
                "  → Preguntá EXACTAMENTE: '¿Cuál querés que analice? ¿[equipoA vs equipoB] o [equipoC vs equipoD]?'\n"
                "  NUNCA emitas ACTION:ANALIZAR sin partido confirmado.\n\n"
                "CASO 2 — CUALQUIER OTRA SITUACIÓN:\n"
                "  → Buscá en la lista de arriba y proponé el partido.\n"
                "  NUNCA emitas ACTION:ANALIZAR sin partido confirmado."
            )
        mensajes.append({"role": "system", "content": inyeccion})
        mensajes.append(history[-1])
    else:
        mensajes += history

    respuesta_completa = ""
    stream = _client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=mensajes,
        temperature=0.55,
        max_tokens=800,
        stream=True
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            respuesta_completa += delta

    session_store.append_message(session_id, "assistant", respuesta_completa)
    return respuesta_completa


def chat_con_ia_analisis(prompt_analisis: str, session_id: str, datos_sofascore: str) -> str:
    """Second Groq call for the actual analysis text (after SofaScore data is fetched)."""
    history = session_store.get_history(session_id)
    mensajes = [
        # #0m: usar SYSTEM_PROMPT con fixtures en TZ del user.
        {"role": "system", "content": _system_prompt_con_tz()},
        {"role": "system", "content": f"DATOS REALES PARA EL ANÁLISIS:\n{datos_sofascore}"},
    ] + history + [{"role": "user", "content": prompt_analisis}]

    respuesta = ""
    stream = _client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=mensajes,
        temperature=0.55,
        max_tokens=800,
        stream=True
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            respuesta += delta

    session_store.replace_last_assistant(session_id, respuesta)
    return respuesta


# ── Startup ──────────────────────────────────────────────────────────

def initialize_engine(progress_cb=None) -> bool:
    """
    Load fixtures and LIGAS at startup.
    Returns True if successful, False if fixtures failed to load.
    """
    global LIGAS, SYSTEM_PROMPT

    if progress_cb: progress_cb("🔄 Verificando predicciones anteriores...")
    try:
        verificar_predicciones(_nueva_sesion())
    except Exception as e:
        if progress_cb: progress_cb(f"⚠️ Error verificando predicciones: {e}")

    if progress_cb: progress_cb("🔄 Cargando fixtures y ligas...")
    try:
        fixtures_texto = cargar_proximos_partidos()
        LIGAS.update(_fl.LIGAS)
        SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + f"\n\n{fixtures_texto}"
        if progress_cb: progress_cb("✅ Fixtures cargados")
        result = True
    except Exception as e:
        if progress_cb: progress_cb(f"⚠️ Error cargando fixtures: {e}")
        SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT
        result = False

    # Iniciar caché de stats colectivas
    if _sc:
        try:
            _sc.refresh_stats()
        except Exception as e:
            print(f"⚠️  No se pudo iniciar stats_colectivas: {e}")

    return result
