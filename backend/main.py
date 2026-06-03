"""
main.py — FastAPI entry point for the Football Analyzer API.

Run with:
    cd backend
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Endpoints:
    GET  /api/health     — health check
    GET  /api/fixtures   — current loaded fixtures
    POST /api/chat       — main chat endpoint (Server-Sent Events)
"""

import asyncio
import json
import os
import re
import sys
import threading
from contextlib import asynccontextmanager

# Force UTF-8 output so emojis in logs don't crash on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import engine
import session_store
import rate_limiter
import quota
from auth import verificar_token


# ── Startup / Shutdown ───────────────────────────────────────────────

def _safe_print(msg: str):
    """Print with emoji-safe fallback for Windows consoles."""
    try:
        print(msg)
    except (UnicodeEncodeError, UnicodeDecodeError):
        print(msg.encode("ascii", errors="replace").decode("ascii"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load fixtures and LIGAS at startup in a background thread."""
    def _init():
        engine.initialize_engine(progress_cb=_safe_print)

    thread = threading.Thread(target=_init, daemon=True)
    thread.start()
    thread.join(timeout=120)  # wait up to 2 min for fixtures to load
    yield
    # Cleanup on shutdown
    session_store.cleanup_expired()


app = FastAPI(title="⚽ Analizador Fútbol API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    # Offset horario del usuario en HORAS desde UTC (ej: -3 para ARG, -6 MX,
    # +2 ESP). Si no viene, el engine cae al default del server.
    tz_offset_hours: float | None = None


# ── Foco label map ───────────────────────────────────────────────────

# Bug #3: el LLM a veces escribe "(Alta )" con espacio sobrante antes
# de cerrar paréntesis. Limpiamos siempre en post-proceso.
def _limpiar_formato(texto: str) -> str:
    if not texto:
        return texto
    texto = re.sub(r'\s+\)', ')', texto)   # "(Alta )" → "(Alta)"
    texto = re.sub(r'\(\s+', '(', texto)   # "( Alta)" → "(Alta)"
    return texto


def _extraer_widget_data(equipo1: str, equipo2: str, lineas_py: dict,
                         prom_eq1: dict, prom_eq2: dict) -> dict:
    """Datos estructurados para los widgets del dashboard frontend."""
    def _total(key):
        entry = lineas_py.get(key)
        return round(entry[0], 2) if entry else None

    def _prom(prom, stat):
        v = prom.get(stat)
        return round(v, 2) if v is not None else None

    # Corners: busca clave exacta primero, luego cualquier corners_antes_X
    corners_avg = _total("corners")
    if corners_avg is None:
        for k, v in lineas_py.items():
            if k.startswith("corners"):
                corners_avg = round(v[0], 2)
                break

    return {
        "team1":       equipo1,
        "team2":       equipo2,
        "corners_avg": corners_avg,
        "shots_home":  _prom(prom_eq1, "ALL_Shots on target"),
        "shots_away":  _prom(prom_eq2, "ALL_Shots on target"),
        "yellow_avg":  _total("tarjetas_amarillas"),
    }


# #0i: el mapeo de aliases de liga vive ahora en engine.py (fuente única).
# Acá solo usamos engine.detectar_liga_en_mensaje() / engine.normalizar_liga().


# Bug #0e: detecta cuándo el usuario está pidiendo EXPLÍCITAMENTE un
# nuevo análisis (o agregar una stat puntual al anterior). Si no
# matchea ninguno y ya hay análisis previo en la sesión → es aclaración.
_RE_NUEVO_ANALISIS = re.compile(
    r'\b(?:'
    r'agregame|agreg[áa]me|agreg[áa]|sum[áa]|sumame|sum[áa]me|'
    r'a[ñn]ad[íi]|a[ñn]ad[íi]me|a[ñn]adir|'
    r'analiz[áa] de nuevo|nuevo an[áa]lisis|otro an[áa]lisis|'
    r'otra vez el an[áa]lisis|volv[éeè] a analizar|'
    r'de nuevo el an[áa]lisis|reanaliz[áa]|'
    r'analiz[áa] otro|analiz[áa] el (?:siguiente|otro)|'
    r'apost(?:ar|emos)|combinada|acumuladora|'
    # confirmaciones de análisis nuevo: "hacelo", "dale", "analizá ese", etc.
    r'hacelo|hac[éeè]lo|analizalo|analiz[áa]lo|'
    r'analiz[áa] ese|analizá ese|analiz[áa] el partido|analizá el partido|'
    r'hac[éeè] el an[áa]lisis|hac[éeè] ese an[áa]lisis|'
    r'and[áa]|dale'
    r')\b|'
    # "sí/ok + verbo de análisis" (prefijo, sin \b al final)
    r'\b(?:s[íi]|ok)\s+(?:haz|hac[éeè]|analiz)|'
    # foco específico nuevo con verbo de futuro/pregunta
    r'(?:cu[aá]nt[oa]s|hab[rr][áa]|va\s+a\s+haber|hay)\s+'
    r'(?:corners?|tarjetas?|goles?|remates?|faltas?|amarillas?|rojas?)',
    re.IGNORECASE,
)


def _es_pedido_nuevo_analisis(msg: str) -> bool:
    return bool(_RE_NUEVO_ANALISIS.search(msg or ""))


def _hay_analisis_previo(history: list) -> bool:
    """True si hay análisis previo en el historial.
    Devuelve False si el assistant mostró recientemente un fixture nuevo:
    en ese caso el usuario probablemente confirma ESE partido, no el anterior."""
    _LIGA_HINTS = (
        "copa", "liga", "league", "premier", "bundesliga", "serie",
        "ligue", "libertadores", "sudamericana", "saudi", "deild",
        "champions", "torneo", "primera", "segunda",
    )
    msgs_assistant = [m for m in reversed(history[-6:]) if m.get("role") == "assistant"]
    for m in msgs_assistant[:2]:
        c = m.get("content", "").lower()
        if " vs " in c and any(kw in c for kw in _LIGA_HINTS):
            return False

    for m in reversed(history[-12:]):
        if m.get("role") != "assistant":
            continue
        c = m.get("content", "")
        if "⚽" in c or "Total esperado" in c or "ACTION:ANALIZAR|" in c:
            return True
    return False


# Bug #0g/#1: marcar local/visitante en cualquier línea de fixture.
# Soporta los dos formatos que generamos:
#   "TeamA vs TeamB (fecha hora)"     (fixtures cargados)
#   "TeamA vs TeamB — torneo — fecha" (buscar_fixture_equipo)
# Idempotente: no agrega si ya están las marcas.
def _marcar_local_visitante(linea: str) -> str:
    if "(L)" in linea or "(V)" in linea:
        return linea
    # Caso "TeamA vs TeamB (..." → inserta antes de "(".
    nueva = re.sub(
        r'^(\s*-?\s*)([^\n(]+?)\s+vs\s+([^\n(]+?)(\s+\()',
        r'\1\2 (L) vs \3 (V)\4',
        linea, count=1,
    )
    if nueva != linea:
        return nueva
    # Caso "TeamA vs TeamB — torneo — fecha" → inserta antes de " — ".
    return re.sub(
        r'^(\s*-?\s*)([^\n—]+?)\s+vs\s+([^\n—]+?)(\s+—)',
        r'\1\2 (L) vs \3 (V)\4',
        linea, count=1,
    )


_FOCO_LABEL = {
    "corners": "Corners", "corners_1h": "Corners 1T", "corners_2h": "Corners 2T",
    "goles": "Goles", "tarjetas_amarillas": "Tarjetas amarillas",
    "tarjetas_amarillas_1h": "Tarjetas AM 1T", "tarjetas_amarillas_2h": "Tarjetas AM 2T",
    "tarjetas_rojas": "Tarjetas rojas", "remates": "Remates al arco",
    "faltas": "Faltas", "faltas_1h": "Faltas 1T", "faltas_2h": "Faltas 2T",
    "completo": "Análisis completo",
}

_COPAS = {"Champions League", "Copa Libertadores", "Copa Sudamericana"}

_MAX_MSG_LEN = 600

# Patrones que intentan sobrescribir instrucciones del sistema
_RE_INJECTION = re.compile(
    r'(ignor[aá](r|me|nos)?\s+(todo|instrucciones|sistema|previo)|'
    r'ignore\s+(all|previous|system)|'
    r'olvid[aá](r|te|nos)?\s+(todo|instrucciones)|'
    r'eres?\s+(ahora|un\s+nuevo)|you\s+are\s+now|'
    r'modo\s+developer|developer\s+mode|'
    r'print\s+(api|secret|key|password|token)|'
    r'ACTION:\s*(ANALIZAR|COMBINADA|BUSCAR))',
    re.IGNORECASE,
)


def _sanitizar_mensaje(msg: str) -> str:
    """Limita longitud y neutraliza patrones de prompt injection."""
    msg = msg[:_MAX_MSG_LEN]
    if _RE_INJECTION.search(msg):
        # Reemplaza el patrón con texto inofensivo en lugar de rechazar,
        # para no romper el flujo pero sí neutralizar la instrucción.
        msg = _RE_INJECTION.sub("[consulta no válida]", msg)
    return msg


# ── Core processing (runs in a thread, sends events via queue) ────────

def _process(message: str, session_id: str, queue: asyncio.Queue,
             loop: asyncio.AbstractEventLoop, user_id: str = "default",
             tz_offset_hours: float | None = None):
    """
    Full message processing flow, adapted from interfaz.py App.procesar().
    Puts SSE events into queue from a background thread using loop.call_soon_threadsafe.

    Event shapes:
        {"event": "status",   "data": '{"message": "..."}'}
        {"event": "response", "data": '{"type": "chat|analysis|combinada|fixture", "content": "...", ...}'}
        {"event": "done",     "data": "{}"}
        {"event": "error",    "data": '{"message": "..."}'}
    """

    def emit(event: str, data: dict):
        loop.call_soon_threadsafe(queue.put_nowait, {"event": event, "data": json.dumps(data)})

    def status(msg: str):
        emit("status", {"message": msg})

    try:
        # Bug #0d: fijar TZ del usuario para todo este request.
        engine.set_request_tz_offset(tz_offset_hours)

        message = _sanitizar_mensaje(message)
        history = session_store.get_history(session_id, user_id)

        es_confirmacion = engine._es_respuesta_a_aclaracion(history)
        es_pred         = engine._es_prediccion(message) or es_confirmacion
        es_schedule     = engine._es_consulta_schedule(message)

        # ── Direct schedule lookup (bypass LLM) ──────────────────────
        if es_schedule and not es_pred:
            equipo = engine._extraer_equipo_schedule(message)
            # Si el texto extraído es muy largo, es una consulta de liga/general
            # — no un nombre de equipo. Dejar que lo maneje el LLM con fixtures.
            if equipo and len(equipo.split()) > 2:
                equipo = None
            # No buscar en historial si el usuario pidió todos los partidos en general
            if not equipo and not engine._es_consulta_todos_partidos(message):
                equipo = engine._extraer_equipo_de_historial(history)

            # ── Consulta genérica: listar todos los partidos del día directamente ──
            if not equipo and engine._es_consulta_todos_partidos(message):
                fixtures_txt = engine._obtener_fixtures_texto()
                liga_filtro = engine.detectar_liga_en_mensaje(message)  # Bug #0f/#0i

                if fixtures_txt:
                    # Si se detectó liga, filtrar fixtures por bloque de liga.
                    # El texto tiene cabeceras tipo "Premier League:" antes de
                    # las líneas "  - TeamA vs TeamB (fecha)".
                    if liga_filtro:
                        lineas_filtradas = []
                        liga_actual = ""
                        for l in fixtures_txt.splitlines():
                            ls = l.strip()
                            if ls.endswith(":") and " vs " not in ls and "===" not in ls:
                                liga_actual = ls[:-1].strip()
                                continue
                            if liga_actual in liga_filtro and " vs " in ls:
                                lineas_filtradas.append(l)
                        lineas_hoy = [
                            l.strip() for l in lineas_filtradas
                            if "[HOY]" in l or "[EN CURSO]" in l
                        ]
                        nombre_liga_pretty = " / ".join(liga_filtro)

                        # #0k: identificar ligas pedidas que NO produjeron
                        # ninguna línea de fixture (potencialmente en receso).
                        ligas_con_lineas = set()
                        _liga_act = ""
                        for l_raw in fixtures_txt.splitlines():
                            _ls = l_raw.strip()
                            if _ls.endswith(":") and " vs " not in _ls and "===" not in _ls:
                                _liga_act = _ls[:-1].strip()
                                continue
                            if _liga_act in liga_filtro and " vs " in _ls:
                                ligas_con_lineas.add(_liga_act)
                        ligas_sin_lineas = [l for l in liga_filtro if l not in ligas_con_lineas]

                        def _nota_receso():
                            if not ligas_sin_lineas:
                                return ""
                            partes = []
                            for l in ligas_sin_lineas:
                                est = engine.estado_liga(l)
                                partes.append("  • " + est.get('mensaje', f"{l}: sin partidos."))
                            return ("\n\nℹ️ Otras ligas que pediste no tienen partidos disponibles:\n"
                                    + "\n".join(partes))

                        if lineas_hoy:
                            texto = f"Acá están los partidos de hoy en {nombre_liga_pretty}:\n" + "\n".join(
                                "• " + _marcar_local_visitante(
                                    re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', '', l).strip()
                                )
                                for l in lineas_hoy
                            ) + _nota_receso()
                        elif lineas_filtradas:
                            # Hay partidos pero no [HOY] → mostrar próximos
                            texto = f"No hay partidos de {nombre_liga_pretty} hoy. Próximos:\n" + "\n".join(
                                "• " + _marcar_local_visitante(l.strip())
                                for l in lineas_filtradas[:10]
                            ) + _nota_receso()
                        else:
                            # #0k: en vez de mensaje genérico, consultar estado
                            # de cada liga pedida (receso, no cargada, próximo).
                            partes = []
                            for l in liga_filtro:
                                est = engine.estado_liga(l)
                                partes.append("• " + est.get('mensaje', f"{l}: sin partidos."))
                            texto = (f"No tengo partidos de {nombre_liga_pretty} hoy:\n"
                                     + "\n".join(partes))
                    else:
                        # Sin filtro: todos los [HOY] de todas las ligas
                        lineas_hoy = [
                            l.strip() for l in fixtures_txt.splitlines()
                            if "[HOY]" in l or "[EN CURSO]" in l
                        ]
                        if lineas_hoy:
                            texto = "Acá están los partidos de hoy:\n" + "\n".join(
                                "• " + _marcar_local_visitante(
                                    re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', '', l).strip()
                                )
                                for l in lineas_hoy
                            )
                        else:
                            texto = "No encontré partidos programados para hoy en las ligas que sigo."
                else:
                    texto = "Los fixtures aún se están cargando, intentá en un momento."
                session_store.append_message(session_id, "user", message)
                session_store.append_message(session_id, "assistant", texto)
                emit("response", {"type": "fixture", "content": texto})
                emit("done", {})
                return

            if equipo:
                status(f"🔍 Buscando partidos de {equipo} en SofaScore...")
                partidos = engine.buscar_fixture_equipo(equipo)
                if not partidos:
                    partidos = engine._buscar_en_fixtures_cargados(equipo)

                if partidos:
                    session_store.append_message(session_id, "user", message)
                    f = partidos[0]
                    es_hoy   = "[HOY]"      in f
                    en_curso = "[EN CURSO]" in f
                    f_limpio = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', ' ', f).strip()
                    sufijo   = " (hoy)"       if es_hoy   else ""
                    sufijo  += " — en curso"  if en_curso else ""
                    texto = f"El próximo partido de {equipo} es:\n• {_marcar_local_visitante(f_limpio)}{sufijo}"
                    session_store.append_message(session_id, "assistant", texto)
                    emit("response", {"type": "fixture", "content": texto})
                    emit("done", {})
                    return
                elif len(equipo.split()) > 1:
                    # Equipo multi-palabra sin resultados → respuesta directa
                    session_store.append_message(session_id, "user", message)
                    texto = f"No encontré próximos partidos de {equipo} en SofaScore."
                    session_store.append_message(session_id, "assistant", texto)
                    emit("response", {"type": "fixture", "content": texto})
                    emit("done", {})
                    return
                # Equipo de una sola palabra sin resultados → probablemente es una
                # consulta de liga ("francesa", "inglesa", etc.). Caemos al LLM
                # con los fixtures inyectados para que liste los partidos de esa liga.

        # ── Call LLM ─────────────────────────────────────────────────
        status("💬 Pensando...")
        respuesta = engine.chat_con_ia(
            message, session_id,
            forzar_action=es_pred,
            es_confirmacion_partido=es_confirmacion,
            forzar_fixtures=es_schedule and not es_pred,
            user_id=user_id,
        )
        respuesta = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', ' ', respuesta).strip()
        respuesta = _limpiar_formato(respuesta)

        # ── ACTION:BUSCAR_FIXTURE ─────────────────────────────────────
        if "ACTION:BUSCAR_FIXTURE|" in respuesta:
            m = re.search(r'ACTION:BUSCAR_FIXTURE\|(.*?)(?:\n|$)', respuesta)
            equipo = m.group(1).strip() if m else None
            if equipo:
                status(f"🔍 Buscando partidos de {equipo} en SofaScore...")
                partidos = engine.buscar_fixture_equipo(equipo)
                if not partidos:
                    partidos = engine._buscar_en_fixtures_cargados(equipo)
                if partidos:
                    f = partidos[0]
                    es_hoy   = "[HOY]"      in f
                    en_curso = "[EN CURSO]" in f
                    f_limpio = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', ' ', f).strip()
                    sufijo   = " (hoy)"       if es_hoy   else ""
                    sufijo  += " — en curso"  if en_curso else ""
                    texto = f"El próximo partido de {equipo} es:\n• {_marcar_local_visitante(f_limpio)}{sufijo}"
                else:
                    texto = f"No encontré próximos partidos de {equipo} en SofaScore."
                session_store.replace_last_assistant(session_id, texto)
                emit("response", {"type": "fixture", "content": texto})
            else:
                # Bug #0b: ACTION:BUSCAR_FIXTURE sin nombre → emitir mensaje
                # claro en vez de cerrar la stream en silencio.
                msg = "No pude identificar el equipo a buscar. Decime el nombre."
                session_store.replace_last_assistant(session_id, msg)
                emit("response", {"type": "chat", "content": msg})
            emit("done", {})
            return

        # ── Guard: consulta de schedule nunca dispara análisis ───────
        # Si el usuario preguntó por horario / fecha / rival (es_schedule)
        # y NO pidió predicción (es_pred=False), descartamos cualquier
        # ACTION:ANALIZAR que el LLM haya generado por inercia de contexto
        # (típico: ya hubo un análisis en la sesión y ahora preguntan
        # "¿cuándo se juega?" o "¿a qué hora?").
        if es_schedule and not es_pred and "ACTION:ANALIZAR|" in respuesta:
            limpia = re.sub(r'ACTION:ANALIZAR\|[^\n]+', '', respuesta).strip()
            if not limpia or len(limpia) < 10:
                fixtures_txt = engine._obtener_fixtures_texto()
                if fixtures_txt:
                    lineas = [l for l in fixtures_txt.splitlines() if l.strip()][:30]
                    limpia = "Estos son los próximos partidos:\n\n" + "\n".join(lineas)
                else:
                    limpia = "No tengo fixtures cargados en este momento."
            session_store.replace_last_assistant(session_id, limpia)
            emit("response", {"type": "chat", "content": limpia})
            emit("done", {})
            return

        # ── Guard: si hay análisis previo y el user NO pidió uno nuevo,
        # descartar ACTION:ANALIZAR del LLM (#0e). Cubre el caso típico
        # "en el análisis hablaste de equipo local pero no aclaraste cuál"
        # — el LLM responde con ACTION:ANALIZAR por inercia de contexto.
        # Excepción: si es confirmación de partido/foco, NO interferir.
        if ("ACTION:ANALIZAR|" in respuesta
                and _hay_analisis_previo(history)
                and not es_confirmacion
                and not _es_pedido_nuevo_analisis(message)):
            limpia = re.sub(r'ACTION:ANALIZAR\|[^\n]+', '', respuesta).strip()
            limpia = _limpiar_formato(limpia)
            if not limpia or len(limpia) < 15:
                limpia = ("Eso lo cubrí en el análisis de arriba. Si querés "
                          "sumar otro mercado (corners, tarjetas, goles, etc.) "
                          "decime cuál y lo agrego.")
            session_store.replace_last_assistant(session_id, limpia)
            emit("response", {"type": "chat", "content": limpia})
            emit("done", {})
            return

        # ── Guard: consulta de schedule nunca dispara combinada ──────
        # Si el usuario preguntó por fixtures/horarios y el LLM (por contexto
        # de sesión) generó una acción de combinada, la descartamos y
        # respondemos con los fixtures directamente.
        if es_schedule and ("ACTION:COMBINADA_AUTO" in respuesta or "ACTION:COMBINADA|" in respuesta):
            fixtures_txt = engine._obtener_fixtures_texto()
            if fixtures_txt:
                lineas = [l for l in fixtures_txt.splitlines() if l.strip()][:30]
                msg = "Estos son los próximos partidos disponibles:\n\n" + "\n".join(lineas)
            else:
                msg = "No hay fixtures cargados en este momento."
            session_store.replace_last_assistant(session_id, msg)
            emit("response", {"type": "chat", "content": msg})
            emit("done", {})
            return

        # ── ACTION:COMBINADA_AUTO ─────────────────────────────────────
        if "ACTION:COMBINADA_AUTO" in respuesta:
            ok, motivo = quota.check_combinada(user_id)
            if not ok:
                session_store.replace_last_assistant(session_id, motivo)
                emit("response", {"type": "chat", "content": motivo})
                emit("done", {})
                return

            m = re.search(r'ACTION:COMBINADA_AUTO(?:\|([^\n|]+))?', respuesta)
            liga_filtro = m.group(1).strip() if (m and m.group(1)) else ""
            status_msg = f"🔍 Buscando combinada de {liga_filtro}..." if liga_filtro else "🔍 Buscando la mejor combinada..."
            status(status_msg)

            picks, debug_info = engine.hacer_combinada_auto(
                n_picks=2, progress_cb=status, liga_filtro=liga_filtro
            )
            texto = engine._formatear_combinada(picks, liga_filtro=liga_filtro, debug_info=debug_info)
            if picks:
                engine._guardar_picks_combinada(picks, user_id=user_id)
            session_store.replace_last_assistant(session_id, texto)
            emit("response", {"type": "combinada", "content": texto, "picks": picks})
            emit("done", {})
            return

        # ── ACTION:COMBINADA (específica) ─────────────────────────────
        if "ACTION:COMBINADA|" in respuesta:
            ok, motivo = quota.check_combinada(user_id)
            if not ok:
                session_store.replace_last_assistant(session_id, motivo)
                emit("response", {"type": "chat", "content": motivo})
                emit("done", {})
                return

            m = re.search(r'ACTION:COMBINADA\|(.*?)(?:\n|$)', respuesta)
            if m:
                raw = m.group(1).strip()
                partidos_picks = []
                for pick_str in raw.split(";"):
                    partes = [p.strip() for p in pick_str.split("|")]
                    if len(partes) >= 4:
                        eq1        = partes[0]; eq2 = partes[1]
                        stats_raw  = partes[2]; liga_n = partes[3]
                        stats_list = (["auto"] if stats_raw.lower() == "auto"
                                      else [s.strip() for s in stats_raw.split(",")])
                        partidos_picks.append((eq1, eq2, stats_list, liga_n))

                if partidos_picks:
                    n_total = len(partidos_picks)
                    status(f"🔄 Analizando combinada ({n_total} partido{'s' if n_total > 1 else ''})...")
                    picks = engine.hacer_combinada_especifica(partidos_picks)
                    texto = engine._formatear_combinada(picks)
                    if picks:
                        engine._guardar_picks_combinada(picks, user_id=user_id)
                    session_store.replace_last_assistant(session_id, texto)
                    emit("response", {"type": "combinada", "content": texto, "picks": picks})
                    emit("done", {})
                    return

        # ── ACTION:ANALIZAR ───────────────────────────────────────────
        if "ACTION:ANALIZAR|" in respuesta:
            ok, motivo = quota.check_analisis(user_id)
            if not ok:
                session_store.replace_last_assistant(session_id, motivo)
                emit("response", {"type": "chat", "content": motivo})
                emit("done", {})
                return

            equipo1 = equipo2 = foco = liga_nombre = None
            match = re.search(r'ACTION:ANALIZAR\|(.*?)\|(.*?)\|(.*?)\|(.*?)(?:\n|$)', respuesta)
            if match:
                equipo1 = match.group(1).strip(); equipo2 = match.group(2).strip()
                foco    = match.group(3).strip(); liga_nombre = match.group(4).strip()
            else:
                partes  = respuesta.split("ACTION:ANALIZAR|")[1].split("|")
                equipo1 = partes[0].strip() if len(partes) > 0 else None
                equipo2 = partes[1].strip() if len(partes) > 1 else None
                foco    = partes[2].strip() if len(partes) > 2 else "completo"
                liga_nombre = partes[3].strip() if len(partes) > 3 else ""

            if not equipo1 or not equipo2:
                # Bug #0b: antes emitíamos solo `done` y el frontend quedaba
                # mudo después de "Pensando...". Siempre emitir un mensaje.
                if not equipo1 and not equipo2:
                    msg = ("No pude identificar el partido. Decime los dos "
                           "equipos (ej: 'analizá Boca vs River').")
                else:
                    eq_dado = equipo1 or equipo2
                    msg = (f"Necesito el rival para analizar a {eq_dado}. "
                           f"Decime contra quién juega.")
                session_store.replace_last_assistant(session_id, msg)
                emit("response", {"type": "chat", "content": msg})
                emit("done", {})
                return

            # ── Validar contra fixtures reales ────────────────────────
            # El LLM puede inventar el rival o la liga. Buscamos el equipo
            # directamente en el texto de fixtures (simple y confiable).
            def _buscar_en_fixtures(nombre: str):
                """
                Recorre el texto de fixtures línea a línea buscando `nombre`.
                Recolecta TODOS los candidatos y devuelve el mejor:
                  1º partidos [HOY] / [EN CURSO]
                  2º cualquier partido futuro (en orden de aparición)
                Devuelve (eq1, eq2, liga, es_hoy) con los datos reales, o None.
                """
                txt = engine._obtener_fixtures_texto()
                if not txt:
                    return None
                n = nombre.lower()
                liga_actual = ""
                candidatos = []   # todos los matches encontrados
                for linea in txt.splitlines():
                    ls = linea.strip()
                    # Cabecera de liga: "Copa Libertadores:"
                    if ls.endswith(":") and " vs " not in ls and "===" not in ls:
                        liga_actual = ls[:-1].strip()
                        continue
                    # Línea de partido: "  - TeamA vs TeamB (fecha...)"
                    if " vs " not in ls or n not in ls.lower():
                        continue
                    es_hoy = "[HOY]" in linea or "[EN CURSO]" in linea
                    m = re.search(r'-?\s*(.+?)\s+vs\s+(.+?)(?:\s*\(|$)', ls)
                    if not m or not liga_actual:
                        continue
                    home = m.group(1).strip()
                    away = m.group(2).strip()
                    if n in home.lower() or home.lower() in n:
                        candidatos.append((home, away, liga_actual, es_hoy))
                    elif n in away.lower() or away.lower() in n:
                        candidatos.append((away, home, liga_actual, es_hoy))
                if not candidatos:
                    return None
                # Preferir partidos de hoy/en curso sobre futuros
                candidatos.sort(key=lambda x: 0 if x[3] else 1)
                return candidatos[0]

            # Bug #0c: si el LLM nombró AMBOS equipos, el partido pedido
            # debe existir como par. No agarrar cualquier match suelto.
            def _buscar_par_en_fixtures(n1: str, n2: str):
                txt = engine._obtener_fixtures_texto()
                if not txt:
                    return None
                l1, l2 = n1.lower(), n2.lower()
                liga_actual = ""
                for linea in txt.splitlines():
                    ls = linea.strip()
                    if ls.endswith(":") and " vs " not in ls and "===" not in ls:
                        liga_actual = ls[:-1].strip()
                        continue
                    if " vs " not in ls:
                        continue
                    low = ls.lower()
                    if not (l1 in low and l2 in low):
                        continue
                    es_hoy = "[HOY]" in linea or "[EN CURSO]" in linea
                    m = re.search(r'-?\s*(.+?)\s+vs\s+(.+?)(?:\s*\(|$)', ls)
                    if not m or not liga_actual:
                        continue
                    home = m.group(1).strip()
                    away = m.group(2).strip()
                    # devolver al "equipo1 pedido" como primer elemento
                    if l1 in home.lower():
                        return (home, away, liga_actual, es_hoy)
                    return (away, home, liga_actual, es_hoy)
                return None

            fix_data = _buscar_par_en_fixtures(equipo1, equipo2)
            par_no_encontrado = False
            if not fix_data:
                # ¿Alguno de los dos existe individualmente? Si SÍ, significa
                # que el PAR pedido no existe (caso "Barcelona vs Boca" donde
                # Boca tiene partido pero no contra Barcelona). Hay que avisar.
                solo_uno = (_buscar_en_fixtures(equipo1)
                            or _buscar_en_fixtures(equipo2))
                if solo_uno:
                    par_no_encontrado = True
                else:
                    # Ninguno de los dos aparece → fallback al comportamiento
                    # anterior (puede ser que el LLM se equivocó con un nombre
                    # pero el otro sí está y vale el análisis).
                    fix_data = (_buscar_en_fixtures(equipo1)
                                or _buscar_en_fixtures(equipo2))

            _safe_print(f"[fixture-fix] buscando '{equipo1}' vs '{equipo2}' → {fix_data} (par_no_encontrado={par_no_encontrado})")

            if par_no_encontrado:
                msg = (f"No encontré {equipo1} vs {equipo2} en los próximos "
                       f"fixtures. Verificá los equipos o pedime un partido distinto.")
                session_store.replace_last_assistant(session_id, msg)
                emit("response", {"type": "chat", "content": msg})
                emit("done", {})
                return

            if fix_data:
                eq1_r, eq2_r, liga_r, es_hoy_fix = fix_data
                equipo1, equipo2, liga_nombre = eq1_r, eq2_r, liga_r

                # Confirmar de forma natural qué partido se va a analizar
                sufijo = " — partido de hoy" if es_hoy_fix else ""
                msg_confirm = (f"Dale, analizando {equipo1} (L) vs {equipo2} (V) "
                               f"({liga_nombre}{sufijo}). Un momento...")
                session_store.replace_last_assistant(session_id, msg_confirm)
                emit("response", {"type": "chat", "content": msg_confirm})
            else:
                msg = (f"No encontré a {equipo1} en los próximos partidos de las ligas "
                       f"disponibles. Solo puedo analizar equipos que figuren en los "
                       f"fixtures cargados.")
                session_store.replace_last_assistant(session_id, msg)
                emit("response", {"type": "chat", "content": msg})
                emit("done", {})
                return

            status("🔄 Bajando datos de SofaScore...")
            foco_lower = foco.lower()

            # ── Rama especial: corners antes del minuto X ──────────────
            if foco_lower.startswith("corners_antes_"):
                try:
                    minuto_x = int(foco_lower.replace("corners_antes_", ""))
                except ValueError:
                    minuto_x = 45
                datos, lineas_py, prom_eq1, prom_eq2 = engine.hacer_analisis_corners_tiempo(
                    equipo1, equipo2, minuto_x, liga_nombre, progress_cb=status
                )
                evento_id  = None
                info_ronda = ""
                _, _li = engine.buscar_liga_info(liga_nombre)
                liga_info = _li or {"id": 0, "temporada": 0}
            else:
                datos, evento_id, info_ronda, liga_info, lineas_py, prom_eq1, prom_eq2 = engine.hacer_analisis_completo(
                    equipo1, equipo2, liga_nombre, progress_cb=status
                )

            # ── Log contexto completo (siempre, incluso si está vacío) ────
            _safe_print("\n" + "="*60)
            _safe_print(f"[CONTEXTO SOFASCORE] {equipo1} vs {equipo2} ({liga_nombre})")
            _safe_print("="*60)
            _safe_print(datos)
            _safe_print("="*60 + "\n")

            # Guard: si no hay stats reales, no pasarle datos vacíos al LLM
            if "(sin datos suficientes)" in datos:
                msg = (f"Encontré el partido {equipo1} vs {equipo2} en los fixtures, "
                       f"pero SofaScore no tiene estadísticas históricas suficientes "
                       f"para hacer el análisis en este momento.")
                session_store.replace_last_assistant(session_id, msg)
                emit("response", {"type": "chat", "content": msg})
                emit("done", {})
                return

            status("🤖 Analizando...")
            if foco_lower.endswith("_1h"):
                instruccion_periodo = "Usá ÚNICAMENTE los datos con prefijo 1ST_ (primer tiempo). NUNCA uses ALL_ ni 2ND_."
            elif foco_lower.endswith("_2h"):
                instruccion_periodo = "Usá ÚNICAMENTE los datos con prefijo 2ND_ (segundo tiempo). NUNCA uses ALL_ ni 1ST_."
            else:
                instruccion_periodo = "Usá los datos con prefijo ALL_ (partido completo)."

            instruccion_foco = engine._FOCO_PROMPT.get(foco_lower, engine._FOCO_PROMPT["completo"])

            ctx_comp = f"COMPETICIÓN: {liga_nombre}"
            if info_ronda: ctx_comp += f" — {info_ronda}"

            nota_comp = (
                "Este partido es de una copa eliminatoria o por fases de grupos. "
                "Considerá si algún equipo podría estar jugándose la clasificación o enfrentando la eliminación: "
                "eso suele elevar la intensidad (más faltas y tarjetas, más corners defensivos, "
                "o más goles si un equipo necesita marcar sí o sí). "
                "Mencioná brevemente este factor y si puede ajustar la línea recomendada al alza."
                if liga_nombre in _COPAS else
                "Considerá si el partido tiene relevancia especial en la tabla "
                "(lucha por el título, zona de copas, pelea por el descenso), "
                "ya que eso puede afectar la intensidad y las stats esperadas."
            )

            # ── Generar párrafos 1 y 2 en Python (garantizado) ───────────
            parrafos_python = engine._generar_parrafos_python(
                foco_lower, equipo1, equipo2, lineas_py, prom_eq1, prom_eq2
            )

            if parrafos_python:
                prompt_analisis = (
                    f"Los siguientes párrafos YA ESTÁN ESCRITOS con los datos reales. "
                    f"Copialos exactamente al inicio de tu respuesta sin modificar ni una palabra:\n\n"
                    f"---\n{parrafos_python}\n---\n\n"
                    f"ÚNICA TAREA: después de los párrafos de arriba, agregá UNA SOLA oración de interpretación "
                    f"(máximo 20 palabras). Respondé: ¿el over es cómodo o ajustado con ese total? "
                    f"¿hay alguna anomalía llamativa (ej: un equipo genera mucho pero concede poco)?\n"
                    f"Si no hay nada concreto que agregar, no escribas nada más.\n\n"
                    f"PROHIBIDO ABSOLUTO:\n"
                    f"- Modificar los párrafos de arriba\n"
                    f"- Agregar más de una oración de interpretación\n"
                    f"- Escribir 'el contexto competitivo', 'los equipos luchan', 'la intensidad'\n"
                    f"- Repetir información ya presente en los párrafos\n\n"
                    f"{ctx_comp}"
                )
            else:
                prompt_analisis = (
                    f"Analizá {equipo1} vs {equipo2}.\n{ctx_comp}\n{instruccion_foco}"
                )

            analisis = engine.chat_con_ia_analisis(prompt_analisis, session_id, datos)
            analisis_limpio = re.sub(r'ACTION:ANALIZAR\|[^\n]+', '', analisis).strip()
            analisis_limpio = _limpiar_formato(analisis_limpio)

            foco_label = _FOCO_LABEL.get(foco_lower, foco)
            header = f"⚽ {equipo1} (L) vs {equipo2} (V)"
            if liga_nombre: header += f"  •  {liga_nombre}"
            if info_ronda:  header += f"  •  {info_ronda}"
            header += f"\n📌 Foco: {foco_label}\n"
            header += "─" * 40

            # Emit the analysis FIRST so the user always sees it,
            # even if guardar_prediccion fails afterwards.
            emit("response", {
                "type":    "analysis",
                "header":  header,
                "content": analisis_limpio,
                "widgets": _extraer_widget_data(equipo1, equipo2, lineas_py, prom_eq1, prom_eq2),
            })

            # ── Extraer stats del análisis para persistir ─────────────
            _prob_mod = _linea_rec = _conf_val = None

            if foco_lower != "completo":
                _entry = lineas_py.get(foco_lower)
                if _entry:
                    from memory import _prob_desde_total
                    _prob_mod  = _prob_desde_total(_entry[0])
                    _linea_rec = _entry[2]
                    _conf_val  = _entry[3]
            else:
                _goles_e = lineas_py.get("goles")
                if _goles_e:
                    from memory import _prob_desde_total
                    _prob_mod  = _prob_desde_total(_goles_e[0])
                    _linea_rec = _goles_e[2]
                    _conf_val  = _goles_e[3]

            # BTTS como fallback de probabilidad cuando no hay línea directa
            if _prob_mod is None and foco_lower in ("goles", "completo"):
                _b1 = prom_eq1.get("btts_score")
                _b2 = prom_eq2.get("btts_score")
                if _b1 is not None and _b2 is not None:
                    _prob_mod = round(min(_b1 * _b2, 0.95), 4)

            # cuota/edge quedan None hasta que se implemente el ingreso de cuotas
            _cuota = None
            _edge  = None

            try:
                engine.guardar_prediccion(
                    equipo1, equipo2, foco, analisis_limpio,
                    evento_id=evento_id,
                    liga_id=liga_info["id"],
                    temporada_id=liga_info["temporada"],
                    user_id=user_id,
                    probabilidad_modelo=_prob_mod,
                    linea_recomendada=_linea_rec,
                    confianza=_conf_val,
                    cuota=_cuota,
                    edge=_edge,
                    liga_nombre=liga_nombre,
                )
            except Exception as _save_err:
                _safe_print(f"[warn] guardar_prediccion falló: {_save_err}")

            emit("done", {})
            return

        # ── Regular chat response ─────────────────────────────────────
        if es_pred:
            es_aclaracion = (
                "?" in respuesta
                and len(respuesta.strip()) < 400
                and not engine._STATS_INVENTADAS.search(respuesta)
            )
            if not es_aclaracion:
                session_store.replace_last_assistant(session_id, engine._MSG_SIN_DATOS)
                emit("response", {"type": "chat", "content": engine._MSG_SIN_DATOS})
                emit("done", {})
                return

        emit("response", {"type": "chat", "content": respuesta})
        emit("done", {})

    except Exception as e:
        emit("error", {"message": str(e)})
        emit("done", {})   # always close the stream so the frontend doesn't hang


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/robots.txt", include_in_schema=False)
async def robots():
    content = "User-agent: *\nDisallow: /docs\nDisallow: /redoc\nSitemap: https://scoutai-b7gn.onrender.com/sitemap.xml\n"
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://scoutai-b7gn.onrender.com/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>"
        "</urlset>"
    )
    from fastapi.responses import Response
    return Response(content, media_type="application/xml")


@app.get("/")
async def serve_frontend():
    html_path = os.path.join(os.path.dirname(__file__), "test_chat.html")
    return FileResponse(html_path, media_type="text/html")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ligas_cargadas": len(engine.LIGAS),
        "fixtures_disponibles": bool(engine.SYSTEM_PROMPT),
    }


@app.get("/api/fixtures")
async def get_fixtures():
    texto = engine._obtener_fixtures_texto()
    if not texto:
        raise HTTPException(status_code=503, detail="Fixtures no cargados aún")
    return {"fixtures": texto}


@app.get("/api/stats")
async def get_stats(http_request: Request):
    auth_header = http_request.headers.get("Authorization")
    try:
        user_id = verificar_token(auth_header)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    from supabase_client import db
    from collections import defaultdict
    from memory import _calcular_racha

    res = db.table("predicciones").select("*").eq("user_id", user_id).order("created_at").execute()
    preds = res.data or []

    total       = len(preds)
    verificadas = [p for p in preds if p.get("acerto") is not None]
    acertadas   = [p for p in verificadas if p.get("acerto") is True]
    n_v = len(verificadas)
    n_a = len(acertadas)

    hit_rate = round(n_a / n_v * 100, 1) if n_v else None

    ganancias = [p["ganancia"] for p in verificadas if p.get("ganancia") is not None]
    roi    = round(sum(ganancias) / len(ganancias) * 100, 1) if ganancias else None
    yield_ = round(sum(ganancias) / total * 100, 1) if (ganancias and total) else None

    verificadas_desc = sorted(verificadas, key=lambda p: p.get("created_at", ""), reverse=True)
    racha_actual = _calcular_racha(verificadas_desc)

    # Por mercado
    mercado_map: dict = defaultdict(lambda: {"total": 0, "verificadas": 0, "acertadas": 0})
    for p in preds:
        fk = p.get("foco") or "desconocido"
        mercado_map[fk]["total"] += 1
        if p.get("acerto") is not None:
            mercado_map[fk]["verificadas"] += 1
            if p.get("acerto") is True:
                mercado_map[fk]["acertadas"] += 1

    por_mercado = []
    for fk, s in sorted(mercado_map.items(), key=lambda x: -x[1]["total"]):
        if s["verificadas"] == 0:
            continue
        hr = round(s["acertadas"] / s["verificadas"] * 100, 1) if s["verificadas"] else None
        por_mercado.append({"foco": fk, "total": s["total"], "acertadas": s["acertadas"],
                            "hit_rate": hr, "roi": None})

    # Por liga
    liga_map: dict = defaultdict(lambda: {"total": 0, "verificadas": 0, "acertadas": 0})
    for p in preds:
        lk = p.get("liga_nombre") or "Sin liga"
        liga_map[lk]["total"] += 1
        if p.get("acerto") is not None:
            liga_map[lk]["verificadas"] += 1
            if p.get("acerto") is True:
                liga_map[lk]["acertadas"] += 1

    por_liga = []
    for lk, s in sorted(liga_map.items(), key=lambda x: -x[1]["total"]):
        if s["verificadas"] == 0:
            continue
        hr = round(s["acertadas"] / s["verificadas"] * 100, 1) if s["verificadas"] else None
        por_liga.append({"liga": lk, "total": s["total"], "acertadas": s["acertadas"],
                         "hit_rate": hr, "roi": None})

    recientes_raw = sorted(preds, key=lambda p: p.get("created_at", ""), reverse=True)[:10]
    recientes = [
        {
            "fecha":             p.get("fecha", ""),
            "equipo1":           p.get("equipo1", ""),
            "equipo2":           p.get("equipo2", ""),
            "foco":              p.get("foco", ""),
            "linea_recomendada": p.get("linea_recomendada"),
            "confianza":         p.get("confianza"),
            "acerto":            p.get("acerto"),
            "liga_nombre":       p.get("liga_nombre"),
        }
        for p in recientes_raw
    ]

    return {
        "general": {
            "total":        total,
            "verificadas":  n_v,
            "acertadas":    n_a,
            "hit_rate":     hit_rate,
            "roi":          roi,
            "yield":        yield_,
            "racha_actual": racha_actual,
        },
        "por_mercado": por_mercado,
        "por_liga":    por_liga,
        "recientes":   recientes,
    }


@app.post("/api/chat")
async def chat(request: ChatRequest, http_request: Request):
    """
    Main chat endpoint. Returns a Server-Sent Events stream.

    Event types emitted:
      status   — progress updates while fetching data
      response — final result {type, content, header?, picks?}
      done     — stream finished
      error    — something went wrong
    """
    # Extraer user_id del JWT (si hay token). Sin token → "default".
    auth_header = http_request.headers.get("Authorization")
    try:
        user_id = verificar_token(auth_header)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    allowed, motivo = rate_limiter.check(user_id)
    if not allowed:
        raise HTTPException(status_code=429, detail=motivo)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    # TZ offset: prioridad body → header X-Timezone-Offset (minutos JS) → None.
    tz_hours = request.tz_offset_hours
    if tz_hours is None:
        raw = http_request.headers.get("X-Timezone-Offset")
        if raw:
            try:
                # JS Date.getTimezoneOffset() devuelve minutos con signo INVERTIDO
                # (ARG = +180). Convertimos a horas con signo correcto.
                tz_hours = -float(raw) / 60.0
            except ValueError:
                tz_hours = None

    def run():
        _process(request.message, request.session_id, queue, loop,
                 user_id=user_id, tz_offset_hours=tz_hours)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    async def generate():
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=180.0)
                yield event
                if event["event"] in ("done", "error"):
                    break
        except asyncio.TimeoutError:
            yield {"event": "error", "data": json.dumps({"message": "Timeout: el servidor tardó demasiado."})}

    return EventSourceResponse(generate())


@app.delete("/api/session/{session_id}")
async def clear_session(session_id: str):
    """Clear chat history for a session."""
    session_store.clear_session(session_id)
    return {"cleared": session_id}
