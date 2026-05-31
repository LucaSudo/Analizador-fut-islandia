from dotenv import load_dotenv
load_dotenv()
from fixture_loader import cargar_proximos_partidos
import os
import re
import time
import threading
from datetime import datetime, date, timedelta
import customtkinter as ctk
from groq import Groq
from curl_cffi import requests as cf_requests
from memory import cargar_memoria, guardar_prediccion, generar_contexto_memoria, verificar_predicciones

# ── Configuración ────────────────────────────────────────────────

API_KEY_GROQ = os.getenv("GROQ_API_KEY")

LIGAS = {}

LIGA_ID = 188
TEMPORADA_ID = 89094
RONDAS_TOTALES = 7

client = Groq(api_key=API_KEY_GROQ)
historial = []

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Lógica SofaScore ─────────────────────────────────────────────

def _nueva_sesion():
    """Crea una sesión curl_cffi que imita el TLS fingerprint de Chrome para evitar bloqueos de SofaScore."""
    return cf_requests.Session(impersonate="chrome124")

def fetch_api(sesion, url):
    return sesion.get(url, timeout=15).json()

MAX_DIAS_HISTORIAL = 60   # no usar partidos de más de 60 días de antigüedad

def obtener_partidos_equipo(sesion, nombre_equipo, ultimas_rondas=5):
    """
    Retorna los últimos N partidos TERMINADOS del equipo, ordenados de más
    reciente a más viejo, descartando partidos de más de MAX_DIAS_HISTORIAL días.
    Excluye partidos en curso y futuros (sus stats están incompletas).
    """
    ahora      = datetime.now().timestamp()
    cutoff     = ahora - MAX_DIAS_HISTORIAL * 86400   # timestamp mínimo aceptable
    partidos   = []

    # Iterar desde la ronda más reciente hacia atrás para obtener los más recientes
    for ronda in range(RONDAS_TOTALES + 1, max(0, RONDAS_TOTALES - ultimas_rondas - 6), -1):
        data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/events/round/{ronda}")
        for evento in data.get("events", []):
            status = evento.get("status", {}).get("type", "")
            start  = evento.get("startTimestamp", 0)

            if status != "finished":
                continue
            if start < cutoff:
                # Partido demasiado antiguo — y como iteramos de reciente a viejo,
                # los siguientes también serán viejos: podemos cortar.
                continue

            home = evento["homeTeam"]["name"]
            away = evento["awayTeam"]["name"]
            if nombre_equipo.lower() in home.lower() or nombre_equipo.lower() in away.lower():
                partidos.append(evento)

        if len(partidos) >= ultimas_rondas:
            break

    # Ordenar por fecha descendente (más reciente primero) y tomar los N pedidos
    partidos.sort(key=lambda e: e.get("startTimestamp", 0), reverse=True)
    return partidos[:ultimas_rondas]

def obtener_estadisticas(sesion, evento_id):
    """Devuelve dict {periodo_stat: {home, away}} para un evento."""
    try:
        data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/event/{evento_id}/statistics")
        stats = {}
        for grupo in data.get("statistics", []):
            periodo = grupo["period"]  # "ALL", "1ST" o "2ND"
            for g in grupo["groups"]:
                for item in g["statisticsItems"]:
                    clave = f"{periodo}_{item['name']}"
                    if clave not in stats:   # evitar duplicados (Total shots aparece 2 veces)
                        stats[clave] = {
                            "home": item.get("home", "?"),
                            "away": item.get("away", "?")
                        }
        return stats
    except:
        return {}

def calcular_lineas_y_confianza(total_esperado: float) -> tuple:
    """
    Retorna (línea_directa, línea_segura, nivel_confianza, línea_conservadora).

    Línea directa     : X.5 inmediatamente inferior al total (la más agresiva).
    Línea segura      : primera línea X.5 con margen ≥ 1.0 (Media o mejor).
    Línea conservadora: primera línea X.5 con margen ≥ 2.5 (Muy Alta).
                        Igual a línea_segura si esta ya alcanza Muy Alta.
    Confianza         : basada en el margen de la línea segura.

    Ejemplos (total=13.60):
      directa=Over 13.5 | segura=Over 12.5 (Media, margen 1.10) | conservadora=Over 10.5 (Muy alta, margen 3.10)
    """
    # ── Línea directa ──────────────────────────────────────────────
    base = int(total_esperado)
    linea_directa = base + 0.5
    if linea_directa >= total_esperado:
        linea_directa -= 1.0

    # ── Línea segura: primer X.5 con margen ≥ 1.0 ─────────────────
    linea_segura = linea_directa
    while total_esperado - linea_segura < 1.0:
        if linea_segura <= 0.5:
            break
        linea_segura -= 1.0
        if linea_segura < 0.5:
            linea_segura = 0.5

    margen = total_esperado - linea_segura

    # ── Nivel de confianza ─────────────────────────────────────────
    if margen >= 2.5:
        confianza = "Muy alta 🟢"
    elif margen >= 1.5:
        confianza = "Alta 🟢"
    elif margen >= 1.0:
        confianza = "Media 🟡"
    else:
        confianza = "Baja 🔴"

    # ── Línea conservadora: primer X.5 con margen ≥ 2.5 (Muy Alta) ─
    # Solo diferente de línea_segura cuando ésta tiene confianza < Muy Alta.
    linea_conservadora = linea_segura
    while total_esperado - linea_conservadora < 2.5:
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

# Stats de conteo que se pueden promediar directamente
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

def precomputar_stats_equipo(sesion, nombre_equipo, n=5):
    """
    Busca los últimos N partidos terminados y calcula promedios EN PYTHON,
    extrayendo el valor correcto (local o visitante) de cada stat.
    Para cada stat se captura tanto el valor propio (FOR) como el del rival (AGAINST),
    lo que permite calcular líneas más precisas combinando ambos.
    """
    partidos     = obtener_partidos_equipo(sesion, nombre_equipo, n)
    acum         = {f"{p}_{s}": [] for p, s in _STATS_A_PRECOMPUTAR}
    acum_against = {f"{p}_{s}": [] for p, s in _STATS_A_PRECOMPUTAR}  # stats del rival
    goles            = []   # goles anotados total
    goles_against    = []   # goles recibidos total
    goles_1h         = []   # goles anotados 1er tiempo
    goles_against_1h = []   # goles recibidos 1er tiempo
    goles_2h         = []   # goles anotados 2do tiempo
    goles_against_2h = []   # goles recibidos 2do tiempo
    refs = []

    for e in partidos:
        home     = e["homeTeam"]["name"]
        away     = e["awayTeam"]["name"]
        es_local = nombre_equipo.lower() in home.lower()
        ronda    = e.get("roundInfo", {}).get("round", "?")

        # Goles totales (anotados y recibidos)
        gh = e.get("homeScore", {}).get("current", None)
        ga = e.get("awayScore", {}).get("current", None)
        if gh is not None and ga is not None:
            goles.append(gh if es_local else ga)
            goles_against.append(ga if es_local else gh)

        # Goles por período
        gh1 = e.get("homeScore", {}).get("period1", None)
        ga1 = e.get("awayScore", {}).get("period1", None)
        gh2 = e.get("homeScore", {}).get("period2", None)
        ga2 = e.get("awayScore", {}).get("period2", None)
        if gh1 is not None and ga1 is not None:
            goles_1h.append(gh1 if es_local else ga1)
            goles_against_1h.append(ga1 if es_local else gh1)
        if gh2 is not None and ga2 is not None:
            goles_2h.append(gh2 if es_local else ga2)
            goles_against_2h.append(ga2 if es_local else gh2)

        fecha_str = (
            datetime.fromtimestamp(e["startTimestamp"]).strftime("%d/%m/%Y")
            if e.get("startTimestamp") else "?"
        )
        refs.append(
            f"{fecha_str} R{ronda}: {home} {gh}-{ga} {away} "
            f"({'local' if es_local else 'visitante'})"
        )

        stats = obtener_estadisticas(sesion, e["id"])
        time.sleep(0.8)   # evitar rate-limiting de SofaScore

        for periodo, stat_name in _STATS_A_PRECOMPUTAR:
            clave = f"{periodo}_{stat_name}"
            if clave not in stats:
                continue
            col_propia  = "home" if es_local else "away"
            col_rival   = "away" if es_local else "home"
            try:
                acum[clave].append(int(str(stats[clave][col_propia])))
            except (ValueError, TypeError):
                pass
            try:
                acum_against[clave].append(int(str(stats[clave][col_rival])))
            except (ValueError, TypeError):
                pass

    # ── Construir texto de salida ────────────────────────────────────
    lineas = [f"ESTADÍSTICAS DE {nombre_equipo.upper()} (últimos {len(partidos)} partidos terminados):"]
    lineas.append(f"  Partidos: {' | '.join(refs)}")

    # Goles totales y por período
    def _linea_goles(nombre, lista):
        if lista:
            lineas.append(f"  {nombre}: {lista} → promedio = {sum(lista)/len(lista):.2f}")

    _linea_goles("Goles anotados",        goles)
    _linea_goles("Goles recibidos",       goles_against)
    _linea_goles("Goles anotados 1T",     goles_1h)
    _linea_goles("Goles recibidos 1T",    goles_against_1h)
    _linea_goles("Goles anotados 2T",     goles_2h)
    _linea_goles("Goles recibidos 2T",    goles_against_2h)

    # Resto de stats: FOR inmediatamente seguido de AGAINST para facilitar lectura
    for periodo, stat_name in _STATS_A_PRECOMPUTAR:
        clave = f"{periodo}_{stat_name}"
        if acum[clave]:
            prom = sum(acum[clave]) / len(acum[clave])
            lineas.append(f"  {clave}: {acum[clave]} → promedio = {prom:.2f}")
        if acum_against[clave]:
            prom_a = sum(acum_against[clave]) / len(acum_against[clave])
            lineas.append(f"  {clave} (concedidos): {acum_against[clave]} → promedio = {prom_a:.2f}")

    # ── Construir dict de promedios ──────────────────────────────────
    promedios = {}

    def _set_prom(key, lista):
        if lista:
            promedios[key] = sum(lista) / len(lista)

    _set_prom("goles",            goles)
    _set_prom("goles_against",    goles_against)
    _set_prom("goles_1h",         goles_1h)
    _set_prom("goles_against_1h", goles_against_1h)
    _set_prom("goles_2h",         goles_2h)
    _set_prom("goles_against_2h", goles_against_2h)

    for periodo, stat_name in _STATS_A_PRECOMPUTAR:
        clave = f"{periodo}_{stat_name}"
        if acum[clave]:
            promedios[clave] = sum(acum[clave]) / len(acum[clave])
        if acum_against[clave]:
            promedios[f"{clave}_against"] = sum(acum_against[clave]) / len(acum_against[clave])

    return "\n".join(lineas), promedios

def formatear_partido(evento, stats):
    """Mantener para uso en memory.py (verificación de predicciones)."""
    home = evento["homeTeam"]["name"]
    away = evento["awayTeam"]["name"]
    gh = evento.get("homeScore", {}).get("current", "?")
    ga = evento.get("awayScore", {}).get("current", "?")
    texto = f"\n  {home} {gh} - {ga} {away}\n"
    claves_interes = [
        "ALL_Corner kicks", "ALL_Yellow cards", "ALL_Red cards",
        "ALL_Shots on target", "ALL_Fouls", "ALL_Total shots",
        "1ST_Corner kicks", "1ST_Yellow cards", "1ST_Shots on target", "1ST_Fouls",
        "2ND_Corner kicks", "2ND_Yellow cards", "2ND_Shots on target", "2ND_Fouls",
    ]
    for clave in claves_interes:
        if clave in stats:
            texto += f"    {clave}: {home}={stats[clave]['home']} | {away}={stats[clave]['away']}\n"
    return texto

def buscar_fixture_equipo(nombre_equipo, dias=4):
    """
    Busca en SofaScore los próximos partidos de un equipo.
    Estrategia de dos pasos:
      1. Endpoint global por fecha  → cubre la mayoría de ligas y Copa Libertadores.
      2. Endpoints last/0 y next/0 por liga → cubre ligas pequeñas (ej: Besta deild karla)
         que no aparecen en el endpoint global.
    """
    sesion     = _nueva_sesion()
    ahora      = datetime.now().timestamp()
    inicio_hoy = datetime.combine(date.today(), datetime.min.time()).timestamp()
    resultados = []
    vistos     = set()

    def _agregar_evento(evento, nombre_torneo=None):
        eid = evento.get("id")
        if eid in vistos:
            return
        home = evento.get("homeTeam", {}).get("name", "")
        away = evento.get("awayTeam", {}).get("name", "")
        if nombre_equipo.lower() not in home.lower() and nombre_equipo.lower() not in away.lower():
            return
        ts          = evento.get("startTimestamp", 0)
        status_type = evento.get("status", {}).get("type", "")
        es_hoy      = inicio_hoy <= ts < inicio_hoy + 86400
        es_futuro   = (
            status_type == "inprogress"
            or (status_type == "notstarted" and ts > ahora)
            or (status_type == "notstarted" and es_hoy)
        )
        if not es_futuro:
            return
        vistos.add(eid)
        torneo = nombre_torneo or (
            evento.get("tournament", {}).get("uniqueTournament", {}).get("name", "")
        )
        hora_str  = datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M") if ts else "horario sin confirmar"
        hoy_tag   = " [HOY]"      if es_hoy                      else ""
        curso_tag = " [EN CURSO]" if status_type == "inprogress" else ""
        resultados.append(f"{home} vs {away} — {torneo} — {hora_str}{hoy_tag}{curso_tag}")

    # ── Paso 1: endpoint global por fecha ───────────────────────────────
    for delta in range(dias):
        fecha_api = (date.today() + timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            data = fetch_api(
                sesion,
                f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{fecha_api}"
            )
            for evento in data.get("events", []):
                _agregar_evento(evento)
        except:
            pass

    # ── Paso 2: endpoints por liga (cubre ligas pequeñas) ────────────────
    if not resultados:
        for nombre_liga, datos_liga in LIGAS.items():
            liga_id      = datos_liga["id"]
            temporada_id = datos_liga["temporada"]
            base = (f"https://www.sofascore.com/api/v1/unique-tournament"
                    f"/{liga_id}/season/{temporada_id}/events")
            for endpoint in ["last/0", "next/0"]:
                try:
                    resp = fetch_api(sesion, f"{base}/{endpoint}")
                    for evento in resp.get("events", []):
                        _agregar_evento(evento, nombre_torneo=nombre_liga)
                except:
                    pass

    return resultados


def hacer_analisis_completo(equipo1, equipo2):
    global RONDAS_TOTALES
    sesion = _nueva_sesion()
    # Actualizar RONDAS_TOTALES a la ronda real antes de buscar partidos
    try:
        rounds_data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/rounds")
        rondas_list = rounds_data.get("rounds", [])
        RONDAS_TOTALES = (
            rounds_data.get("currentRound", {}).get("round")
            or (rondas_list[-1].get("round", RONDAS_TOTALES) if rondas_list else RONDAS_TOTALES)
        )
    except:
        pass

    # Pre-calcular stats en Python para cada equipo.
    stats_eq1, promedios_eq1 = precomputar_stats_equipo(sesion, equipo1)
    stats_eq2, promedios_eq2 = precomputar_stats_equipo(sesion, equipo2)

    # Buscar el próximo partido entre los dos equipos
    ahora = datetime.now().timestamp()
    inicio_hoy = datetime.combine(date.today(), datetime.min.time()).timestamp()
    evento_id_proximo = None
    info_ronda = ""   # fase/ronda del partido (ej: "Octavos de final", "Ronda 8")
    ronda_inicio = max(1, RONDAS_TOTALES - 1)
    for ronda in range(ronda_inicio, RONDAS_TOTALES + 7):
        data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/events/round/{ronda}")
        for evento in data.get("events", []):
            home   = evento["homeTeam"]["name"]
            away   = evento["awayTeam"]["name"]
            status = evento.get("status", {}).get("type", "")
            start  = evento.get("startTimestamp", 0)
            equipo1_match = equipo1.lower() in home.lower() or equipo1.lower() in away.lower()
            equipo2_match = equipo2.lower() in home.lower() or equipo2.lower() in away.lower()
            es_hoy = inicio_hoy <= start < inicio_hoy + 86400
            es_vigente = (
                status == "inprogress"
                or (status == "notstarted" and start > ahora)
                or (status == "notstarted" and es_hoy)
            )
            if equipo1_match and equipo2_match and es_vigente:
                evento_id_proximo = evento["id"]
                # Capturar fase/ronda para contexto competitivo
                ri = evento.get("roundInfo", {})
                info_ronda = ri.get("name", "") or (f"Ronda {ri['round']}" if ri.get("round") else "")
                break
        if evento_id_proximo:
            break

    # Calcular líneas de apuesta en Python para las stats principales
    _FOCO_A_CLAVE = {
        "corners":            "ALL_Corner kicks",
        "corners_1h":         "1ST_Corner kicks",
        "corners_2h":         "2ND_Corner kicks",
        "goles":              "goles",
        "tarjetas_amarillas": "ALL_Yellow cards",
        "tarjetas_amarillas_1h": "1ST_Yellow cards",
        "tarjetas_amarillas_2h": "2ND_Yellow cards",
        "remates":            "ALL_Shots on target",
        "remates_1h":         "1ST_Shots on target",
        "remates_2h":         "2ND_Shots on target",
        "faltas":             "ALL_Fouls",
        "faltas_1h":          "1ST_Fouls",
        "faltas_2h":          "2ND_Fouls",
    }
    lineas_python = {}
    for foco_key, stat_clave in _FOCO_A_CLAVE.items():
        v1 = promedios_eq1.get(stat_clave)
        v2 = promedios_eq2.get(stat_clave)
        if v1 is None or v2 is None:
            continue
        # Fórmula: total = (FOR_eq1 + FOR_eq2 + AGAINST_eq1 + AGAINST_eq2) / 2
        # Combina el ataque propio de cada equipo con la defensa del rival.
        a1 = promedios_eq1.get(f"{stat_clave}_against")
        a2 = promedios_eq2.get(f"{stat_clave}_against")
        if a1 is not None and a2 is not None:
            total = (v1 + v2 + a1 + a2) / 2
        else:
            total = v1 + v2   # fallback si no hay datos de concedidos
        linea_directa, linea_segura, confianza, linea_conservadora = calcular_lineas_y_confianza(total)
        lineas_python[foco_key] = (total, linea_directa, linea_segura, confianza, linea_conservadora)

    # Agregar al contexto las líneas ya calculadas
    lineas_ctx = []
    for foco_key, (total, linea_directa, linea_segura, confianza, linea_conservadora) in lineas_python.items():
        ctx = (
            f"  {foco_key}: total esperado = {total:.2f}"
            f" | línea directa = {linea_directa}"
            f" | LÍNEA RECOMENDADA = {linea_segura} (confianza: {confianza})"
        )
        if linea_conservadora != linea_segura:
            ctx += f" | LÍNEA CONSERVADORA = {linea_conservadora} (confianza: Muy alta 🟢)"
        lineas_ctx.append(ctx)

    contexto = (
        "DATOS REALES DE SOFASCORE (promedios ya calculados por equipo):\n\n"
        f"{stats_eq1}\n\n"
        f"{stats_eq2}\n\n"
        "LÍNEAS DE APUESTA PRE-CALCULADAS POR PYTHON:\n"
        "  (directa = más agresiva | RECOMENDADA = margen ≥ 1.0 | CONSERVADORA = margen ≥ 2.5, Muy Alta)\n"
        + ("\n".join(lineas_ctx) if lineas_ctx else "  (sin datos suficientes)")
        + "\n"
    )
    return contexto, evento_id_proximo, info_ronda, lineas_python, promedios_eq1, promedios_eq2


# ── Corners antes del minuto X ────────────────────────────────────────
# SofaScore no expone el minuto exacto de cada corner en su API pública.
# Usamos interpolación lineal sobre los datos de 1T y 2T que ya tenemos:
#   min ≤ 45 → proporción del primer tiempo  = corners_1T * (min/45)
#   min > 45 → primer tiempo completo + fracción del segundo = corners_1T + corners_2T * ((min-45)/45)

def _interpolar_corners(prom: dict, minuto: int) -> tuple:
    """
    Estima corners generados/concedidos antes del minuto X
    usando interpolación lineal sobre los promedios de 1T y 2T.
    Retorna (generados, concedidos) o (None, None) si no hay datos.
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


def hacer_analisis_corners_tiempo(equipo1: str, equipo2: str, minuto: int):
    """
    Análisis de corners antes del minuto X usando interpolación lineal
    sobre los promedios de 1T/2T de SofaScore.
    Retorna (contexto, lineas_python, prom1, prom2).
    """
    global RONDAS_TOTALES
    sesion = _nueva_sesion()

    try:
        rd = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/rounds")
        rl = rd.get("rounds", [])
        RONDAS_TOTALES = (
            rd.get("currentRound", {}).get("round")
            or (rl[-1].get("round", RONDAS_TOTALES) if rl else RONDAS_TOTALES)
        )
    except Exception:
        pass

    # Reutilizamos precomputar_stats_equipo — ya trae 1ST y 2ND corners
    _, prom_raw1 = precomputar_stats_equipo(sesion, equipo1)
    _, prom_raw2 = precomputar_stats_equipo(sesion, equipo2)

    foco_key = f"corners_antes_{minuto}"

    gen1, con1 = _interpolar_corners(prom_raw1, minuto)
    gen2, con2 = _interpolar_corners(prom_raw2, minuto)

    # Construir promedios
    prom1, prom2 = {}, {}
    if gen1 is not None: prom1[foco_key]              = gen1
    if con1 is not None: prom1[f"{foco_key}_against"] = con1
    if gen2 is not None: prom2[foco_key]              = gen2
    if con2 is not None: prom2[f"{foco_key}_against"] = con2

    # Calcular líneas
    lineas_python = {}
    v1, v2 = prom1.get(foco_key), prom2.get(foco_key)
    if v1 is not None and v2 is not None:
        a1, a2 = prom1.get(f"{foco_key}_against"), prom2.get(f"{foco_key}_against")
        total = (v1 + v2 + a1 + a2) / 2 if (a1 is not None and a2 is not None) else v1 + v2
        d, s, c, cons = calcular_lineas_y_confianza(total)
        lineas_python[foco_key] = (total, d, s, c, cons)

    # Contexto
    def _fmt(v):
        return f"{v:.2f}" if v is not None else "(sin datos)"

    nota = f"(estimado por interpolación lineal sobre promedios de 1T/2T)"
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


# ── Generador de párrafos en Python (sin depender del LLM) ───────────

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
                              lineas_python: dict, prom1: dict, prom2: dict) -> str | None:
    """
    Genera los párrafos de datos y líneas completamente en Python.
    Retorna el texto pre-construido, o None si no hay datos para este foco.
    El LLM solo agrega una oración de interpretación al final.
    Soporta focos dinámicos: corners_antes_{minuto}.
    """
    if foco not in lineas_python:
        return None

    total, directa, recomendada, confianza, conservadora = lineas_python[foco]

    # Foco dinámico: corners_antes_X
    if foco.startswith("corners_antes_"):
        minuto = foco.replace("corners_antes_", "")
        stat_label = f"corners antes del minuto {minuto}"
        stat_clave = foco   # la clave en prom1/prom2 es el mismo foco_key
    else:
        stat_clave = _FOCO_A_CLAVE_STAT.get(foco)
        stat_label = _STAT_GEN_LABEL.get(foco, foco)

    # ── Párrafo 1: datos por equipo ───────────────────────────────────
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

    # ── Párrafo 2: líneas ─────────────────────────────────────────────
    p2 = f"La línea directa es {directa}. La apuesta recomendada es {recomendada} ({confianza})."
    if conservadora != recomendada:
        p2 += f" Para los más conservadores: {conservadora} (Muy alta)."

    return f"{p1}\n\n{p2}"


# ── Chat con IA ──────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos un experto en fútbol que charla con un amigo apostador. Respondés de forma natural, directa y humana — nada de lenguaje corporativo ni informes aburridos.

════════════════════════════════════════
TONO Y FORMATO — SIEMPRE APLICAR
════════════════════════════════════════

TONO:
- Hablás como un tipo que sabe mucho de fútbol y te lo explica de manera simple.
- Podés usar expresiones como "la verdad es que", "ojo que", "te digo", "igual", "no te voy a mentir".
- Nunca sonas a robot. Nunca sonas a informe de consultoría.
- Para preguntas simples (horarios, curiosidades, datos): respondés en 1-3 oraciones. No inflés la respuesta.

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
  - "¿Cuándo juega X?" → buscás en tu lista de fixtures. Si está → dás la fecha y hora. Si NO está → decís "No tengo ese partido en mis fixtures, verificalo en SofaScore."
  - "¿A qué hora juega X?" → buscás en fixtures. Si está → dás la hora exacta. Si NO está → decís "No tengo el horario, verificalo en SofaScore."
  - "¿Cuál es el próximo partido de X?" → buscás en fixtures. Si está → lo nombrás. Si NO está → decís que no lo tenés cargado.
  - "¿Dónde juega X?" → información general. Nada más.
  - "Dame un partido de X liga" / "Decime un partido de X" / "Hay partidos de X hoy?" →
      Revisás tu lista de próximos partidos y listás los de esa liga directamente.
      Si hay partidos → los listás (sin preguntar permiso ni ofrecer buscar).
      Si no hay → "No tengo partidos de [liga] cargados actualmente."
      NUNCA preguntes "¿Querés que busque?" ni "¿Querés que haga eso?".
      NUNCA emitas ACTION:BUSCAR_FIXTURE ni ACTION:ANALIZAR para responder esto.
  - "¿Quién es el goleador de X?" → información general. Nada más.
  - "Contame sobre el equipo X" → información general. Nada más.
  - "¿Viste que hoy juega X?" / "¿Sabés que juega X hoy?" / "¿Sabés que X juega contra Y?" →
      BUSCÁS en tu lista de fixtures si ese equipo aparece.
      Si SÍ está → confirmás con los datos exactos del fixture (rival, fecha, hora).
      Si NO está → respondés: "No lo veo en mis fixtures actuales, verificalo en SofaScore."
      NUNCA inventes el rival, la hora ni la competición desde tu memoria de entrenamiento.
  - Cualquier pregunta informativa, histórica o general → respondés directo. Nada más.

NINGUNO de estos casos activa ACTION:ANALIZAR, aunque mencionen un partido, un equipo o un resultado. Que el usuario mencione un partido NO es un pedido de análisis.

PROHIBIDO en cualquier situación:
  - "¿Querés que haga eso?" / "¿Querés que busque?" / "¿Te busco?" / "¿Querés que lo analice?"
  → NUNCA hagas preguntas de oferta. Si tenés que buscar → usá ACTION:BUSCAR_FIXTURE directamente.
    Si no corresponde buscar → respondé con lo que tenés o decí que no lo tenés. Punto.

════════════════════════════════════════
REGLA ABSOLUTA N°3 — CONFIRMACIÓN DE PARTIDO ANTES DE ANALIZAR
════════════════════════════════════════

Antes de disparar ACTION:ANALIZAR, necesitás tener CLARO de qué partido específico se habla.

CASO A — Disparar directo SIN preguntar nada. Aplica cuando:
  – El usuario nombró AMBOS equipos, o
  – El usuario mencionó un equipo + dijo "de hoy" y hay exactamente UN partido [HOY] para ese equipo, o
  – El contexto de la conversación ya dejó claro el partido.
→ Identificás el partido en los fixtures y disparás ACTION:ANALIZAR directamente.
→ Ejemplo: "partido de boca de hoy" + Boca tiene [HOY] vs Universidad Católica → disparás directo.
→ Ejemplo: "cuántos corners en el Valur de hoy" → Valur tiene [HOY] → disparás directo.

CASO B — El usuario menciona un equipo sin especificar, y ese equipo tiene MÁS DE UN partido próximo:
→ Preguntás listando las opciones con datos exactos de los fixtures:
  "¿De qué partido hablás? [Equipo] tiene:
   – [rival1] ([fecha1])
   – [rival2] ([fecha2])"
→ NO disparás ACTION:ANALIZAR hasta que el usuario elija.

CASO C — El usuario menciona un equipo sin especificar "de hoy", y ese equipo tiene UN SOLO partido próximo:
→ Proponés con los datos del fixture: "¿Hablás del partido vs [rival] ([competición], [fecha hora])?"
→ NUNCA preguntés "¿contra quién juega?" si ya tenés el rival en los fixtures. Siempre proponés vos.
→ Esperás confirmación. Recién ahí disparás ACTION:ANALIZAR.

CASO D — El usuario confirma el partido ("sí", "ese", "el de KR", "el de hoy", el nombre del rival, etc.):
→ Si la pregunta que motivó la aclaración era un pedido de PREDICCIÓN o ANÁLISIS:
  Disparás ACTION:ANALIZAR|equipo_local|equipo_visitante|foco|liga con el partido confirmado
  y el foco de la pregunta original (corners, goles, etc.).
→ Si la pregunta original era solo informativa (cuándo juega, dónde, etc.):
  Respondés con la info solicitada. No disparás ACTION:ANALIZAR.

════════════════════════════════════════
REGLA ABSOLUTA N°4 — NOMBRES DE EQUIPOS AMBIGUOS
════════════════════════════════════════

Si el usuario menciona un equipo con un nombre incompleto, apodo, ciudad sola o nombre parcial que pueda referirse a más de un equipo:
→ SIEMPRE preguntás: "¿Te referís a [opción1] o [opción2]?"
→ NUNCA asumís cuál es sin confirmación explícita del usuario.
→ Recién cuando el usuario confirma el equipo, respondés lo que te preguntó.

════════════════════════════════════════
REGLA ABSOLUTA N°5 — NUNCA INVENTÉS DATOS
════════════════════════════════════════

JAMÁS inventés ni estimés estadísticas, goles, corners, tarjetas ni ningún dato numérico sin tener datos reales de SofaScore.

CASOS DONDE SIEMPRE DEBÉS USAR ACTION:ANALIZAR (sin excepción posible):
  - El usuario pregunta cuántos goles/corners/tarjetas/remates/faltas habrá en un partido
  - El usuario pide una apuesta o recomendación numérica
  - El usuario pregunta el resultado más probable
  - El usuario dice "necesito una apuesta segura" o similar

Si no tenés datos reales → NO inventés promedios, NO calculés nada → disparás ACTION:ANALIZAR para obtenerlos.
NUNCA respondás una pregunta de predicción numérica con estadísticas recordadas de entrenamiento.
Inventar estadísticas es el error más grave que podés cometer.

════════════════════════════════════════
REGLA ABSOLUTA N°6 — FORMATO DE ACTION:ANALIZAR
════════════════════════════════════════

Cuando corresponde usar ACTION:ANALIZAR, el formato es EXACTAMENTE este:

  ACTION:ANALIZAR|equipo1|equipo2|foco|liga

Reglas de formato que NO se pueden violar:
  1. ACTION:ANALIZAR va SIEMPRE al FINAL de tu mensaje. Nunca al principio. Nunca en el medio.
  2. FOCO CUANDO EL USUARIO PIDE MÚLTIPLES STATS:
     Si el usuario pide análisis de más de una estadística en la misma pregunta
     (ej: "corners y goles", "tarjetas y corners", "todo", "apuesta segura" sin especificar),
     usá SIEMPRE foco="completo".
     foco="completo" cubre en el análisis: ganador probable, goles, corners,
     tarjetas amarillas, tarjetas rojas, remates al arco y faltas.
  3. El foco debe ser exactamente uno de los siguientes valores:
       completo            — análisis general + ganador probable
       goles               — cantidad total de goles
       corners             — corners totales (partido completo)
       corners_1h          — corners solo en el primer tiempo
       corners_2h          — corners solo en el segundo tiempo
       tarjetas_amarillas  — tarjetas amarillas totales
       tarjetas_amarillas_1h — amarillas en el primer tiempo
       tarjetas_amarillas_2h — amarillas en el segundo tiempo
       tarjetas_rojas      — tarjetas rojas totales
       tarjetas_rojas_1h   — rojas en el primer tiempo
       tarjetas_rojas_2h   — rojas en el segundo tiempo
       remates             — remates al arco totales
       remates_1h          — remates al arco en el primer tiempo
       remates_2h          — remates al arco en el segundo tiempo
       faltas              — faltas totales
       faltas_1h           — faltas en el primer tiempo
       faltas_2h           — faltas en el segundo tiempo
       corners_antes_{minuto} — corners antes del minuto X (ej: corners_antes_30, corners_antes_60, corners_antes_75)
                             Usá el número exacto que pida el usuario. Cualquier minuto entre 1 y 89 es válido.
  3. La liga debe ser exactamente uno de estos valores (copiado tal cual, sin variaciones):
       - Besta deild karla
       - 1. deild karla
       - La Liga
       - Premier League
       - Serie A
       - Bundesliga
       - Ligue 1
       - Ligue 2
       - Champions League
       - Liga Argentina
       - Copa Libertadores
       - Copa Sudamericana
       - Saudi Pro League

════════════════════════════════════════
REGLA ABSOLUTA N°7 — DATOS POR TIEMPO DE JUEGO
════════════════════════════════════════

Los datos de SofaScore vienen en tres bloques:
  - ALL_  → partido completo (45' + 45')
  - 1ST_  → solo el primer tiempo (primeros 45')
  - 2ND_  → solo el segundo tiempo (últimos 45')

Ejemplo de stats disponibles por período:
  ALL_Corner kicks, 1ST_Corner kicks, 2ND_Corner kicks
  ALL_Yellow cards, 1ST_Yellow cards, 2ND_Yellow cards
  ALL_Red cards,    1ST_Red cards,    2ND_Red cards
  ALL_Shots on target, 1ST_Shots on target, 2ND_Shots on target
  ALL_Fouls,        1ST_Fouls,        2ND_Fouls

Regla de uso:
  - Foco termina en _1h  → usás ÚNICAMENTE prefijo 1ST_. NUNCA ALL_ ni 2ND_.
  - Foco termina en _2h  → usás ÚNICAMENTE prefijo 2ND_. NUNCA ALL_ ni 1ST_.
  - Foco sin sufijo      → usás los datos ALL_.

Mezclar prefijos en un mismo análisis está prohibido.

Cuando el análisis es por tiempo, calculá los promedios SOLO con los datos del período correspondiente y terminá la recomendación con el período explícito, por ejemplo: "Recomendación: Over 4.5 corners en el primer tiempo".

════════════════════════════════════════
REGLA ABSOLUTA N°8 — HONESTIDAD SOBRE TU CONTEXTO DE FIXTURES
════════════════════════════════════════

Tu contexto de fixtures se carga al arrancar la app y puede estar desactualizado o incompleto.

REGLAS ESTRICTAS:
  - Si el usuario menciona un partido que NO aparece en tu lista de fixtures →
    NO digas "sí, lo tengo cargado". Di: "No lo veo en mis fixtures actuales,
    pero puedo buscar los datos en SofaScore" y disparás ACTION:ANALIZAR igual.
  - Si el usuario te corrige sobre un fixture (rival incorrecto, fecha distinta) →
    Aceptás la corrección sin discutir y disparás ACTION:ANALIZAR con los datos correctos.
  - Si no tenés la hora de un partido → decís "no tengo el horario exacto,
    podés verificarlo en SofaScore o en la página de la liga".
  - NUNCA inventés fechas, horas ni rivales de partidos.
  - NUNCA confirmes que tenés un partido en tu contexto si no está explícitamente
    en la lista de próximos partidos que aparece más abajo.

════════════════════════════════════════
REGLA ABSOLUTA N°9 — PARTIDOS DE HOY
════════════════════════════════════════

En tu lista de próximos partidos, los que tienen la etiqueta [HOY] corresponden
al día de hoy. Son los más urgentes.

REGLAS:
  - Si el usuario pregunta "cuándo juega X" o "el próximo partido de X", buscá
    primero en los que tienen [HOY]. Si hay uno, ese es el PRÓXIMO partido.
  - NO saltes al siguiente partido sin mencionar primero el de hoy.
  - Si el partido dice [EN CURSO], informá que el partido ya comenzó.

════════════════════════════════════════
LIGAS CON ACCESO A DATOS EN TIEMPO REAL
════════════════════════════════════════

Tenés acceso a datos en tiempo real de:
  - Besta deild karla (Islandia - primera división)
  - 1. deild karla (Islandia - segunda división)
  - La Liga (España)
  - Premier League (Inglaterra)
  - Serie A (Italia)
  - Bundesliga (Alemania)
  - Ligue 1 (Francia)
  - Ligue 2 (Francia - segunda división)
  - Champions League
  - Liga Argentina
  - Copa Libertadores (Sudamérica)
  - Copa Sudamericana (Sudamérica)
  - Saudi Pro League (Arabia Saudita)

════════════════════════════════════════
REGLA ABSOLUTA N°10 — BUSCAR FIXTURE EN TIEMPO REAL
════════════════════════════════════════

Cuando el usuario pregunte contra quién juega un equipo, cuándo juega, o a qué hora,
y ese equipo NO aparece en tu lista de fixtures (o no estás seguro):
→ Emití al FINAL de tu respuesta: ACTION:BUSCAR_FIXTURE|nombre_del_equipo

Ejemplos que activan ACTION:BUSCAR_FIXTURE:
  - "¿Contra quién juega Coquimbo hoy?" → ACTION:BUSCAR_FIXTURE|Coquimbo Unido
  - "¿A qué hora juega Nacional?" → ACTION:BUSCAR_FIXTURE|Club Nacional de Montevideo
  - "¿Cuándo juega River?" → ACTION:BUSCAR_FIXTURE|River Plate

NUNCA inventes rival, hora ni competición. Si no lo ves en fixtures → ACTION:BUSCAR_FIXTURE.

════════════════════════════════════════
REGLA ABSOLUTA N°11 — APUESTAS COMBINADAS
════════════════════════════════════════

Cuando el usuario pida una "apuesta combinada", "combinada", "acumuladora", "combina X con Y",
"armame una combinada", "agregame una", "agrega una para X", "sumar una de X":

CASO A — El usuario NO especifica equipos ni partidos (ej: "armame una combinada", "quiero una combinada"):
→ Escribís UNA frase breve y al final:
  ACTION:COMBINADA_AUTO

  IMPORTANTE: Si el usuario dice "agregame una para [equipo]" o "agrega el partido de [equipo]",
  NO es CASO A — es CASO B. Buscá ese equipo en los fixtures y usá el partido que encuentres.

  Si el usuario menciona una liga o copa específica, incluís el nombre exacto después de una barra.
  Mapeo de menciones comunes → nombre exacto a usar:

  "libertadores" / "copa lib"          → ACTION:COMBINADA_AUTO|Copa Libertadores
  "sudamericana" / "copa sud"          → ACTION:COMBINADA_AUTO|Copa Sudamericana
  "champions" / "champions league"     → ACTION:COMBINADA_AUTO|Champions League
  "la liga" / "laliga" / "españa"      → ACTION:COMBINADA_AUTO|La Liga
  "premier" / "premier league"         → ACTION:COMBINADA_AUTO|Premier League
  "serie a" / "italia"                 → ACTION:COMBINADA_AUTO|Serie A
  "bundesliga" / "alemania"            → ACTION:COMBINADA_AUTO|Bundesliga
  "ligue 1" / "francia"               → ACTION:COMBINADA_AUTO|Ligue 1
  "ligue 2" / "segunda francesa"      → ACTION:COMBINADA_AUTO|Ligue 2
  "liga argentina" / "argentina"       → ACTION:COMBINADA_AUTO|Liga Argentina
  "saudi" / "arabia saudita"           → ACTION:COMBINADA_AUTO|Saudi Pro League
  "besta deild" / "islandia primera"   → ACTION:COMBINADA_AUTO|Besta deild karla
  "1. deild" / "islandia segunda"      → ACTION:COMBINADA_AUTO|1. deild karla

  Si el usuario no menciona liga → ACTION:COMBINADA_AUTO (sin barra).

CASO B — El usuario especifica UN partido (mencionando un equipo o ambos), con o sin stats:
  Ejemplos: "combiname corners + goles de Valur vs KR", "agregame una para boca", "agrega el partido de Palmeiras"
→ Buscá el partido en los fixtures. Al final emitís:
  ACTION:COMBINADA|equipo_local|equipo_visitante|stat1,stat2|liga
  Si no especificó stats → usá "auto":
  ACTION:COMBINADA|equipo_local|equipo_visitante|auto|liga
  Ejemplo: "agregame una para boca" → buscás Boca Juniors en fixtures → "Boca Juniors vs Universidad Católica" →
  ACTION:COMBINADA|Boca Juniors|Universidad Católica|auto|Copa Libertadores

CASO C — El usuario especifica VARIOS partidos (ej: "corners de Valur vs KR y goles de Breidablik vs Víkingur"):
→ Cada pick separado por ";":
  ACTION:COMBINADA|eq1a|eq2a|stat_a|liga_a;eq1b|eq2b|stat_b|liga_b

Stats válidas: corners, goles, tarjetas_amarillas, tarjetas_rojas, remates, faltas
(y variantes _1h / _2h si pide por tiempo)

REGLAS:
- ACTION:COMBINADA o ACTION:COMBINADA_AUTO va SIEMPRE AL FINAL del mensaje.
- NUNCA mezcles ACTION:COMBINADA con ACTION:ANALIZAR.
- Si el usuario pide una combinada auto pero también menciona un partido específico → usá CASO B o C.
"""
# ── Detección Python de pedidos de predicción ────────────────────
# No confiar solo en el LLM para decidir cuándo usar ACTION:ANALIZAR.
# Esta función detecta la intención antes de llamar al modelo.
_PRED_KEYWORDS = [
    "habra", "habrá", "va a haber", "crees que",
    "apostar", "apuesta",
    "over ", "under ",
    "quién gana", "quien gana",
    "prediccion", "predicción", "pronostico", "pronóstico",
    "analizá", "analiza el partido",
    # combinadas
    "combinada", "acumuladora", "combina ", "armame", "arma una",
    "dame una combinada", "quiero una combinada",
    "agregame", "agrega ", "agrega un", "agrega una",
    "sumar ", "suma un", "suma una", "añadir", "añadí",
]
# "cuántos/cuántas" solo es predicción cuando va seguido de una stat de partido
_PRED_STAT_RE = re.compile(
    r'cu[aá]nt[oa]s?\s+(goles?|corners?|tarjetas?|amarillas?|rojas?|faltas?|remates?|tiros?)',
    re.IGNORECASE
)

def _es_prediccion(msg: str) -> bool:
    m = msg.lower()
    return any(kw in m for kw in _PRED_KEYWORDS) or bool(_PRED_STAT_RE.search(msg))

# Detectar consultas de horario/rival — para inyectar fixtures y evitar que el LLM invente
_SCHEDULE_RE = re.compile(
    r'contra\s+qui[eé]n|qui[eé]n\s+juega|cu[aá]ndo\s+juega|a\s+qu[eé]\s+hora|'
    r'el\s+pr[oó]ximo\s+partido|hoy\s+juega|juega\s+hoy|'
    r'sab[eé]s\s+que.{0,40}juega|viste\s+que.{0,40}juega|'
    r'dec[íi]me\s+(un\s+)?partido|dec[íi]rme\s+(un\s+)?partido|'
    r'qu[eé]\s+partidos?\s+hay|dame\s+(los\s+|un\s+)?partidos?',
    re.IGNORECASE
)

def _es_consulta_schedule(msg: str) -> bool:
    return bool(_SCHEDULE_RE.search(msg))

# Palabras a ignorar para extraer el nombre del equipo de una consulta de horario
_SCHED_STOPWORDS = {
    'contra', 'quien', 'quién', 'juega', 'juegan', 'hoy', 'cuando', 'cuándo',
    'hora', 'que', 'qué', 'a', 'el', 'la', 'los', 'las', 'de', 'del',
    'en', 'por', 'para', 'es', 'son', 'sabe', 'sabes', 'sabias', 'sabías',
    'viste', 'me', 'te', 'lo', 'un', 'una', 'al', 'con', 'si', 'sí', 'no',
    'ya', 'proximo', 'próximo', 'partido', 'siguiente', 'cual', 'cuál',
    'y', 'e', 'o', 'u',  # conjunciones
}

def _extraer_equipo_schedule(msg: str) -> str | None:
    """
    Elimina palabras vacías y devuelve lo que queda como nombre de equipo.
    Ej: 'Contra quien juega coquimbo hoy' → 'coquimbo'
        'A que hora juega River Plate'   → 'River Plate'
    """
    palabras = re.sub(r'[?!.,]', '', msg.strip()).split()
    resto = [p for p in palabras if p.lower() not in _SCHED_STOPWORDS]
    return ' '.join(resto).strip() or None

def _extraer_equipo_de_historial() -> str | None:
    """
    Busca en el historial reciente el equipo más relevante para un follow-up
    como 'Juega hoy?' o 'A que hora?'.
    Prioridad:
      1. Mensajes RECIENTES del USUARIO (excluye el actual) que mencionen un equipo.
      2. Fallback: último mensaje del BOT con formato 'Próximos partidos de X:'.
    """
    msgs = historial[-10:]  # ventana de contexto

    # 1. Buscar en mensajes del usuario (de más reciente a más viejo), excluir el último
    msgs_usuario = [m for m in msgs if m["role"] == "user"]
    for msg in reversed(msgs_usuario[:-1] if len(msgs_usuario) > 1 else []):
        equipo = _extraer_equipo_schedule(msg["content"])
        # Filtrar ruido: si tiene más de 3 palabras probablemente no es un equipo
        if equipo and len(equipo.split()) <= 3:
            return equipo

    # 2. Fallback: último mensaje del bot con fixture listado
    for msg in reversed(msgs):
        if msg["role"] == "assistant":
            content = msg["content"]
            m = re.search(r'[Pp]r[oó]ximos\s+partidos\s+de\s+([^:\n]+):', content)
            if m:
                return m.group(1).strip()
            m = re.search(r'^([A-ZÁÉÍÓÚa-záéíóú][^\n]{2,40}?)\s+juega\s+contra', content)
            if m:
                return m.group(1).strip()
    return None

def _obtener_fixtures_texto() -> str:
    """
    Extrae la sección '=== PRÓXIMOS PARTIDOS ===' del SYSTEM_PROMPT completa,
    sin límite de caracteres (el límite anterior de 2000 cortaba ligas que
    aparecían más abajo como Copa Libertadores o Sudamericana).
    """
    start = SYSTEM_PROMPT.find("=== PRÓXIMOS PARTIDOS")
    if start == -1:
        return ""
    # Buscar el siguiente bloque de "===" para saber dónde termina la sección
    next_section = SYSTEM_PROMPT.find("\n===", start + 5)
    if next_section == -1:
        return SYSTEM_PROMPT[start:]
    return SYSTEM_PROMPT[start:next_section]

def _buscar_en_fixtures_cargados(nombre_equipo: str) -> list[str]:
    """
    Busca el equipo en los fixtures cargados al arrancar la app (SYSTEM_PROMPT).
    Sirve de fallback cuando buscar_fixture_equipo no encuentra nada en el
    endpoint global de SofaScore (ej: ligas pequeñas como Besta deild karla).
    """
    fixtures_texto = _obtener_fixtures_texto()
    if not fixtures_texto:
        return []
    resultados = []
    for linea in fixtures_texto.splitlines():
        if nombre_equipo.lower() in linea.lower() and ' vs ' in linea:
            es_hoy_f   = '[HOY]'      in linea
            en_curso_f = '[EN CURSO]' in linea
            limpia = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', '', linea).strip()
            limpia = limpia.lstrip('- ').strip()
            sufijo  = ' (hoy)'       if es_hoy_f   else ''
            sufijo += ' — en curso'  if en_curso_f else ''
            resultados.append(f"{limpia}{sufijo}")
    return resultados

def _es_respuesta_a_aclaracion_partido() -> bool:
    """
    Devuelve True si el último mensaje del asistente fue una pregunta de aclaración
    sobre cuál partido analizar (ej: '¿De qué partido hablás?', '¿Hablás del partido...?').
    Sirve para detectar que el usuario está CONFIRMANDO el partido, no haciendo una
    pregunta nueva — y así forzar que el LLM dispare ACTION:ANALIZAR.
    """
    if not historial:
        return False
    for msg in reversed(historial):
        if msg["role"] == "assistant":
            c = msg["content"].lower()
            return (
                "¿de qué partido" in c
                or "de qué partido hablás" in c
                or "¿hablás del partido" in c
                or "hablás del partido" in c
            )
    return False

# Patrón para detectar si el bot inventó estadísticas sin datos reales
_STATS_INVENTADAS = re.compile(
    r'promedio\s+(?:de\s+)?\d|'         # "promedio de 6" o "promedio 3.60"
    r'\(\s*\d+\s*\+\s*\d+|'             # "(3+2+..."
    r'/\s*\d+\s*=\s*\d|'               # "/ 10 = 3"
    r'recomendaci[oó]n:\s*.{0,60}\d|'  # "Recomendación: Over 2.5"
    r'\bover\s+\d+[.,]\d|'             # "Over 9.5"
    r'l[ií]nea\s+(?:de\s+)?apuesta',   # "línea de apuesta"
    re.IGNORECASE
)

_MSG_SIN_DATOS = (
    "No tengo datos reales de SofaScore para darte eso. "
    "Pedime que analice el partido y lo busco en tiempo real. "
    "Ejemplo: \"analizá Valur vs KR\" o \"cuántos corners habrá en el partido\"."
)

# ── Instrucciones de análisis por foco ───────────────────────────
# Cada valor describe EXACTAMENTE qué debe calcular el LLM.
# El LLM NO debe salirse de estas instrucciones.
# NOTA: Los datos ya llegan pre-calculados en formato:
#   "ALL_Corner kicks: [6, 4, 1, 4, 3] -> promedio = 3.60"
# Y la sección LÍNEAS PRE-CALCULADAS POR PYTHON tiene el total y la línea lista.
# Los prompts solo necesitan decirle al LLM QUÉ stat mirar y cómo presentarlo.

# Template base que se reutiliza en todos los focos de stats numéricas.
# Se instancia con el nombre de la stat y la clave en LÍNEAS PRE-CALCULADAS.
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
        f"  - repetir en el párrafo 3 información ya dicha en los párrafos 1 o 2\n"
        f"  - agregar un cuarto párrafo"
    )

_FOCO_PROMPT = {
    "completo": (
        "Respondé con UN párrafo por stat, en este orden: goles, corners, tarjetas amarillas. "
        "Para cada stat el párrafo debe tener EXACTAMENTE esta estructura:\n"
        "  '[EQ1] anota/genera X y concede Z, [EQ2] anota/genera Y y concede W. "
        "Total esperado: [NÚMERO EXACTO de LÍNEAS PRE-CALCULADAS]. "
        "Línea directa [DIRECTA], recomendada [RECOMENDADA] ([CONFIANZA])"
        "[, conservadora [CONSERVADORA] (Muy alta) si existe].'\n"
        "Separar cada stat con una línea en blanco. "
        "PROHIBIDO: párrafos de contexto vago ('la intensidad', 'la tabla', etc.). "
        "Solo datos y líneas."
    ),
    "goles":              _tpl("goles", "goles"),
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

# ── Apuestas combinadas ───────────────────────────────────────────

# Stats que se evalúan al armar una combinada (en orden de popularidad para apuestas)
_STATS_COMBINADA = [
    ("corners",            "ALL_Corner kicks"),
    ("goles",              "goles"),
    ("tarjetas_amarillas", "ALL_Yellow cards"),
    ("faltas",             "ALL_Fouls"),
    ("remates",            "ALL_Shots on target"),
]

# Línea segura mínima para que un pick sea considerado "útil" en una combinada.
# Picks por debajo de este umbral se descartan (ej. Over 0.5 goles es trivial
# y los bookmakers dan cuotas de 1.02 — sin valor real).
_LINEA_MINIMA_COMBINADA: dict[str, float] = {
    "goles":              1.5,   # Over 0.5 no aporta valor
    "corners":            4.5,   # Over 3.5 o menos es muy bajo
    "tarjetas_amarillas": 1.5,   # Over 0.5 tarjetas es casi seguro
    "tarjetas_rojas":     0.5,   # 0.5 es válido (las rojas son raras)
    "remates":            3.5,   # Over 2.5 remates al arco es muy bajo
    "faltas":            10.5,   # Over 9.5 faltas es casi garantizado
}

# Orden numérico de confianza (menor = mejor)
_ORDEN_CONFIANZA = {
    "Muy alta 🟢": 0,
    "Alta 🟢":     1,
    "Media 🟡":    2,
    "Baja 🔴":     3,
}

# Nombres legibles de stats para el output
_STAT_NOMBRE_ES = {
    "corners":            "Corners totales",
    "corners_1h":         "Corners 1er tiempo",
    "corners_2h":         "Corners 2do tiempo",
    "goles":              "Goles totales",
    "tarjetas_amarillas": "Tarjetas amarillas",
    "tarjetas_amarillas_1h": "Amarillas 1er tiempo",
    "tarjetas_amarillas_2h": "Amarillas 2do tiempo",
    "tarjetas_rojas":     "Tarjetas rojas",
    "faltas":             "Faltas totales",
    "faltas_1h":          "Faltas 1er tiempo",
    "faltas_2h":          "Faltas 2do tiempo",
    "remates":            "Remates al arco",
    "remates_1h":         "Remates al arco 1T",
    "remates_2h":         "Remates al arco 2T",
}

# Mapa completo de stats para combinada (incluye variantes _1h/_2h)
_STATS_COMBINADA_MAPA = {
    "corners":               "ALL_Corner kicks",
    "corners_1h":            "1ST_Corner kicks",
    "corners_2h":            "2ND_Corner kicks",
    "goles":                 "goles",
    "tarjetas_amarillas":    "ALL_Yellow cards",
    "tarjetas_amarillas_1h": "1ST_Yellow cards",
    "tarjetas_amarillas_2h": "2ND_Yellow cards",
    "tarjetas_rojas":        "ALL_Red cards",
    "faltas":                "ALL_Fouls",
    "faltas_1h":             "1ST_Fouls",
    "faltas_2h":             "2ND_Fouls",
    "remates":               "ALL_Shots on target",
    "remates_1h":            "1ST_Shots on target",
    "remates_2h":            "2ND_Shots on target",
}


def _parsear_partidos_fixtures() -> list[tuple]:
    """
    Extrae todos los partidos del SYSTEM_PROMPT (sección PRÓXIMOS PARTIDOS).
    Retorna [(home, away, liga_nombre, es_prioritario), ...]
    ordenados: HOY / EN CURSO primero, luego el resto.
    """
    fixtures_texto = _obtener_fixtures_texto()
    if not fixtures_texto:
        return []

    resultados = []
    liga_actual = ""

    for linea in fixtures_texto.splitlines():
        linea_strip = linea.strip()

        # Detectar encabezado de liga: "Besta deild karla:" (sin guión inicial)
        if linea_strip.endswith(":") and not linea_strip.startswith("-") and "===" not in linea_strip:
            candidato = linea_strip[:-1].strip()
            if candidato:
                liga_actual = candidato
            continue

        # Detectar partido: "  - Home vs Away (fecha...)"
        m = re.search(r'-\s+(.+?)\s+vs\s+(.+?)\s+\(', linea)
        if m and liga_actual:
            home = m.group(1).strip()
            away = m.group(2).strip()
            es_prioritario = "[HOY]" in linea or "[EN CURSO]" in linea
            resultados.append((home, away, liga_actual, es_prioritario))

    # HOY / EN CURSO primero
    resultados.sort(key=lambda x: 0 if x[3] else 1)
    return resultados


def _calcular_picks_partido(sesion, eq1: str, eq2: str, liga_nombre: str,
                             stats_keys: list | None = None) -> list[dict]:
    """
    Descarga stats de eq1 y eq2 y calcula candidatos de apuesta.
    stats_keys: lista de claves de _STATS_COMBINADA_MAPA a evaluar.
                None = evaluar las 5 stats principales (_STATS_COMBINADA).
    Retorna lista de dicts con toda la info de cada pick.
    """
    global LIGA_ID, TEMPORADA_ID, RONDAS_TOTALES

    liga = next((v for k, v in LIGAS.items() if liga_nombre in k or k in liga_nombre), None)
    if not liga:
        return []

    LIGA_ID        = liga["id"]
    TEMPORADA_ID   = liga["temporada"]
    RONDAS_TOTALES = liga["rondas"]

    # Actualizar ronda real
    try:
        rd = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/"
                               f"{LIGA_ID}/season/{TEMPORADA_ID}/rounds")
        rl = rd.get("rounds", [])
        RONDAS_TOTALES = (
            rd.get("currentRound", {}).get("round")
            or (rl[-1].get("round", RONDAS_TOTALES) if rl else RONDAS_TOTALES)
        )
    except Exception:
        pass

    try:
        _, prom1 = precomputar_stats_equipo(sesion, eq1)
        _, prom2 = precomputar_stats_equipo(sesion, eq2)
    except Exception:
        return []

    # Determinar qué stats evaluar
    if stats_keys is None:
        stats_a_evaluar = _STATS_COMBINADA          # lista de (key, clave_sofascore)
    else:
        stats_a_evaluar = [
            (k, _STATS_COMBINADA_MAPA[k])
            for k in stats_keys
            if k in _STATS_COMBINADA_MAPA
        ]

    picks = []
    for stat_key, stat_clave in stats_a_evaluar:
        v1 = prom1.get(stat_clave)
        v2 = prom2.get(stat_clave)
        if v1 is None or v2 is None:
            continue
        a1 = prom1.get(f"{stat_clave}_against")
        a2 = prom2.get(f"{stat_clave}_against")
        total = (v1 + v2 + a1 + a2) / 2 if (a1 is not None and a2 is not None) else v1 + v2

        linea_directa, linea_segura, confianza, _ = calcular_lineas_y_confianza(total)

        # Descartar picks cuya línea segura sea trivialmente baja (sin valor real)
        # Solo aplicar en combinada auto; las combinadas específicas no se filtran.
        if stats_keys is None:   # solo en modo auto
            minima = _LINEA_MINIMA_COMBINADA.get(stat_key.split("_")[0], 0.5)
            val_linea = float(linea_segura.replace("Over ", ""))
            if val_linea < minima:
                continue

        picks.append({
            "partido":       f"{eq1} vs {eq2}",
            "equipo1":       eq1,
            "equipo2":       eq2,
            "liga":          liga_nombre,
            "stat":          stat_key,
            "total":         total,
            "linea_directa": linea_directa,
            "linea_segura":  linea_segura,
            "confianza":     confianza,
        })

    return picks


def hacer_combinada_auto(n_picks: int = 2, progress_cb=None,
                         liga_filtro: str = "") -> list[dict]:
    """
    Escanea los fixtures disponibles, calcula picks para cada partido,
    y retorna los N mejores ordenados por confianza.
    Prefiere picks de partidos distintos para diversificar.
    Si hay partidos de HOY, usa solo esos. Si no hay ninguno, usa los próximos.
    liga_filtro: si se especifica, solo analiza partidos de esa liga (match parcial, case-insensitive).
    """
    partidos = _parsear_partidos_fixtures()
    if not partidos:
        return [], {"n_liga": 0, "n_analizados": 0, "partidos": []}

    # Aplicar filtro de liga si el usuario especificó una
    if liga_filtro:
        filtro_lower = liga_filtro.lower()
        partidos = [p for p in partidos if filtro_lower in p[2].lower()]

    if not partidos:
        return [], {"n_liga": 0, "n_analizados": 0, "partidos": []}

    n_liga = len(partidos)

    # Prioridad: partidos de HOY / EN CURSO. Si no hay, usar próximos disponibles.
    partidos_hoy = [p for p in partidos if p[3]]   # es_prioritario = True
    candidatos   = partidos_hoy if partidos_hoy else partidos

    sesion      = _nueva_sesion()
    todos_picks = []
    partidos_analizados = []

    for i, (home, away, liga_nombre, _) in enumerate(candidatos[:4]):   # máx 4 partidos
        if progress_cb:
            progress_cb(f"🔍 Analizando partido {i+1}/{min(len(candidatos), 4)}: {home} vs {away}...")
        partidos_analizados.append(f"{home} vs {away} ({liga_nombre})")
        picks = _calcular_picks_partido(sesion, home, away, liga_nombre)
        todos_picks.extend(picks)

    debug_info = {
        "n_liga":      n_liga,
        "n_analizados": len(partidos_analizados),
        "partidos":    partidos_analizados,
    }

    if not todos_picks:
        return [], debug_info

    # Ordenar por confianza (mejor primero)
    todos_picks.sort(key=lambda x: _ORDEN_CONFIANZA.get(x["confianza"], 99))

    # Seleccionar diversificando: preferir partidos distintos
    picks_finales     = []
    partidos_usados   = set()
    stats_por_partido = {}   # partido → [stats ya elegidas]

    # 1ª pasada: el mejor pick de cada partido distinto
    for pick in todos_picks:
        p = pick["partido"]
        if p not in partidos_usados:
            picks_finales.append(pick)
            partidos_usados.add(p)
            stats_por_partido[p] = [pick["stat"]]
        if len(picks_finales) >= n_picks:
            break

    # 2ª pasada: si faltan, agregar más picks (stat diferente del mismo partido)
    if len(picks_finales) < n_picks:
        for pick in todos_picks:
            if pick in picks_finales:
                continue
            p, s = pick["partido"], pick["stat"]
            if s not in stats_por_partido.get(p, []):
                picks_finales.append(pick)
                stats_por_partido.setdefault(p, []).append(s)
            if len(picks_finales) >= n_picks:
                break

    return picks_finales[:max(n_picks, 2)], debug_info


def _guardar_picks_combinada(picks: list[dict]) -> None:
    """
    Guarda cada pick de una combinada como predicción independiente en memoria.json.
    Sin evento_id (no hay auto-verificación), pero el LLM lo usa para calibrar.
    """
    from fixture_loader import LIGAS as _LIGAS_FL
    for pick in picks:
        stat     = pick["stat"]
        eq1      = pick["equipo1"]
        eq2      = pick["equipo2"]
        liga_nom = pick["liga"]

        # Texto con formato compatible con _extraer_prediccion_numerica()
        stat_nombre = _STAT_NOMBRE_ES.get(stat, stat)
        pred_texto = (
            f"[Combinada] Recomendación: {pick['linea_segura']} {stat_nombre}. "
            f"Total esperado: {pick['total']:.2f} | Confianza: {pick['confianza']} "
            f"(línea directa: {pick['linea_directa']})"
        )

        # Liga ID y temporada (si están disponibles)
        liga_info    = next((v for k, v in _LIGAS_FL.items()
                             if liga_nom in k or k in liga_nom), None)
        liga_id_save = liga_info["id"]       if liga_info else None
        temp_id_save = liga_info["temporada"] if liga_info else None

        guardar_prediccion(
            equipo1=eq1,
            equipo2=eq2,
            foco=stat,
            prediccion=pred_texto,
            evento_id=None,          # sin evento_id → no se auto-verifica
            liga_id=liga_id_save,
            temporada_id=temp_id_save,
        )


def hacer_combinada_especifica(partidos_picks: list[tuple]) -> list[dict]:
    """
    partidos_picks: [(eq1, eq2, [stat1, stat2, ...], liga_nombre), ...]
    Analiza cada partido con las stats pedidas. Si stats=['auto'], evalúa las 5 principales.
    Retorna todos los picks ordenados por confianza.
    """
    sesion      = _nueva_sesion()
    todos_picks = []

    for eq1, eq2, stats_pedidas, liga_nombre in partidos_picks:
        keys = None if stats_pedidas == ["auto"] else stats_pedidas
        picks = _calcular_picks_partido(sesion, eq1, eq2, liga_nombre, keys)
        todos_picks.extend(picks)

    todos_picks.sort(key=lambda x: _ORDEN_CONFIANZA.get(x["confianza"], 99))
    return todos_picks


def _formatear_combinada(picks: list[dict], liga_filtro: str = "",
                         debug_info: dict | None = None) -> str:
    """
    Genera el texto final de la combinada para mostrar al usuario.
    """
    if not picks:
        liga_msg  = f" de {liga_filtro}" if liga_filtro else ""
        info      = debug_info or {}
        n_liga    = info.get("n_liga", -1)
        n_anal    = info.get("n_analizados", 0)
        partidos  = info.get("partidos", [])

        if n_liga == 0:
            # No había ningún partido de esa liga en los fixtures
            return (
                f"No hay partidos{liga_msg} cargados en los fixtures. "
                "Puede que la liga no tenga partidos próximos o que no se hayan podido cargar al arrancar la app."
            )
        elif n_anal > 0:
            # Había partidos pero no generaron picks confiables
            lista = "\n".join(f"  • {p}" for p in partidos)
            return (
                f"Analicé {n_anal} partido(s){liga_msg} pero ninguno generó picks con suficiente confianza:\n"
                f"{lista}\n"
                "Probá pedir una combinada específica indicando el partido y las stats."
            )
        else:
            return (
                f"No encontré picks{liga_msg} con suficiente confianza para armar una combinada. "
                "Puede que no haya fixtures cargados para hoy, o que los datos no sean suficientes."
            )

    # Confianza combinada = probabilidad individual multiplicada por cada pick
    # (más picks = riesgo exponencialmente mayor)
    _PROB_CONFIANZA = {
        "Muy alta 🟢": 0.88,
        "Alta 🟢":     0.73,
        "Media 🟡":    0.58,
        "Baja 🔴":     0.42,
    }
    prob_combinada = 1.0
    for p in picks:
        prob_combinada *= _PROB_CONFIANZA.get(p["confianza"], 0.50)

    if prob_combinada >= 0.72:
        confianza_combinada = "Muy alta 🟢"
    elif prob_combinada >= 0.55:
        confianza_combinada = "Alta 🟢"
    elif prob_combinada >= 0.38:
        confianza_combinada = "Media 🟡"
    else:
        confianza_combinada = "Baja 🔴"

    lineas = [f"🎯 APUESTA COMBINADA ({len(picks)} selecciones)\n"]

    for i, pick in enumerate(picks, 1):
        stat_nombre = _STAT_NOMBRE_ES.get(pick["stat"], pick["stat"])
        lineas.append(
            f"\nSelección {i}: {pick['linea_segura']} {stat_nombre}\n"
            f"  {pick['equipo1']} vs {pick['equipo2']} — {pick['liga']}\n"
            f"  Total esperado: {pick['total']:.2f} | Confianza: {pick['confianza']}\n"
            f"  (línea directa: {pick['linea_directa']})"
        )

    lineas.append(
        f"\n📊 Confianza combinada: {confianza_combinada} "
        f"(prob. estimada: {prob_combinada*100:.0f}% — {len(picks)} selecciones multiplicadas)"
    )
    lineas.append("⚠️ Todas las selecciones deben entrar para ganar. "
                  "A mayor número de picks, menor probabilidad combinada.")
    lineas.append("⚠️ Solo una recomendación estadística. Los resultados pueden variar.")

    return "\n".join(lineas)


def chat_con_ia(mensaje, datos_sofascore=None, callback=None, forzar_action=False,
                es_confirmacion_partido=False, forzar_fixtures=False):
    historial.append({"role": "user", "content": mensaje})

    contexto_memoria = generar_contexto_memoria()
    system_completo = SYSTEM_PROMPT
    if contexto_memoria:
        system_completo += f"\n\n{contexto_memoria}"

    mensajes = [{"role": "system", "content": system_completo}]

    if datos_sofascore:
        mensajes.append({
            "role": "system",
            "content": f"DATOS REALES PARA EL ANÁLISIS:\n{datos_sofascore}"
        })

    if forzar_fixtures and historial and not forzar_action:
        fixtures_ctx = _obtener_fixtures_texto()
        mensajes += historial[:-1]
        inyeccion = (
            "⚠️ El usuario pregunta sobre horario o rival de un equipo. "
            "Buscá ese equipo ÚNICAMENTE en esta lista:\n\n"
            f"{fixtures_ctx}\n\n"
            "REGLAS ESTRICTAS:\n"
            "- Si el equipo ESTÁ → respondé con los datos exactos (rival, fecha, hora) de la lista.\n"
            "- Si NO está → respondé: 'No lo veo en mis fixtures actuales. "
            "Podés verificarlo en SofaScore.'\n"
            "- NUNCA uses tu memoria de entrenamiento para datos de partidos.\n"
            "- NUNCA menciones [HOY], [EN CURSO] ni ningún formato interno en tu respuesta."
        )
        mensajes.append({"role": "system", "content": inyeccion})
        mensajes.append(historial[-1])
    elif forzar_action and historial:
        # Inyectar recordatorio urgente justo ANTES del último mensaje del usuario.
        # Dos modos distintos según si el usuario está CONFIRMANDO un partido (tras
        # una pregunta de aclaración previa) o haciendo una NUEVA pregunta de predicción.
        mensajes += historial[:-1]
        if es_confirmacion_partido:
            # El usuario acaba de confirmar qué partido quiere analizar.
            # El historial ya tiene el foco original y el partido pedido.
            inyeccion = (
                "⚠️ El usuario confirmó el partido. "
                "En la conversación ya tenés: el foco original de la pregunta (corners, goles, etc.) "
                "y el partido que el usuario acaba de especificar. "
                "Respondé con UNA frase muy corta ('Perfecto, voy a analizar...') y terminá con:\n"
                "ACTION:ANALIZAR|equipo_local|equipo_visitante|foco|liga\n"
                "Asegurate de usar los nombres EXACTOS de los equipos tal como aparecen en los fixtures. "
                "El equipo local es el primero listado en el fixture (home). "
                "NUNCA inventés datos ni promedios."
            )
        else:
            # Nueva pregunta de predicción — el partido puede o no estar claro.
            # Inyectamos el listado real de fixtures para que el LLM no invente
            # nombres ni fechas al preguntar por aclaración.
            fixtures_ctx = _obtener_fixtures_texto()
            fixtures_bloque = (
                f"\nLISTA EXACTA DE PRÓXIMOS PARTIDOS (usá SOLO estos datos):\n{fixtures_ctx}\n"
                if fixtures_ctx else ""
            )
            inyeccion = (
                "⚠️ ACCIÓN REQUERIDA — El usuario pide una PREDICCIÓN o ESTADÍSTICA. "
                "Seguí EXACTAMENTE estas reglas:\n"
                f"{fixtures_bloque}\n"
                "CASO 1 — El partido es ABSOLUTAMENTE CLARO:\n"
                "  ÚNICAMENTE si el usuario nombró AMBOS equipos explícitamente en su mensaje,\n"
                "  O dijo 'de hoy'/'hoy' y hay EXACTAMENTE un partido [HOY] de ese equipo.\n"
                "  → Identificás ese partido, escribís UNA frase corta y terminás con:\n"
                "  ACTION:ANALIZAR|equipo_local|equipo_visitante|foco|liga\n\n"
                "CASO 2 — CUALQUIER OTRA SITUACIÓN (usuario mencionó solo UN equipo sin\n"
                "  aclarar cuál partido, no dijo 'hoy', o hay varios partidos próximos):\n"
                "  → Usá la LISTA DE ARRIBA para buscar los partidos del equipo mencionado.\n"
                "    Si tiene 1 partido: '¿Hablás del partido [Equipo] vs [Rival] el [fecha]?'\n"
                "    Si tiene 2+ partidos: '¿De qué partido hablás? g[Equipo] tiene [partido1 fecha] y [partido2 fecha].'\n"
                "  CRÍTICO: los nombres de equipos y fechas deben ser EXACTAMENTE los de la lista.\n"
                "  NUNCA inventes ni cambies fechas. NO emitas ACTION:ANALIZAR. Esperá al usuario.\n\n"
                "Focos válidos: corners, corners_1h, corners_2h, goles, tarjetas_amarillas, "
                "tarjetas_rojas, remates, faltas, completo (y variantes _1h/_2h).\n"
                "NUNCA inventés estadísticas. NUNCA emitas ACTION:ANALIZAR sin partido confirmado."
            )
        mensajes.append({"role": "system", "content": inyeccion})
        mensajes.append(historial[-1])
    else:
        mensajes += historial

    respuesta_completa = ""
    pending = ""
    action_started = False

    stream = client.chat.completions.create(
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

            if callback and not action_started:
                pending += delta
                idx = pending.find("ACTION:")
                if idx != -1:
                    action_started = True
                    if idx > 0:
                        callback(pending[:idx])
                    pending = ""
                else:
                    flush_up_to = len(pending)
                    for plen in range(min(6, len(pending)), 0, -1):
                        if pending[-plen:] == "ACTION:"[:plen]:
                            flush_up_to = len(pending) - plen
                            break
                    if flush_up_to > 0:
                        callback(pending[:flush_up_to])
                    pending = pending[flush_up_to:]

    if callback and not action_started and pending:
        callback(pending)

    historial.append({"role": "assistant", "content": respuesta_completa})
    return respuesta_completa

# ── Interfaz ─────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self, fixtures=""):
        super().__init__()
        global SYSTEM_PROMPT
        SYSTEM_PROMPT += f"\n\n{fixtures}"
        self.title("⚽ Chat Fútbol")
        self.geometry("750x650")
        self.resizable(True, True)
        self.bind("<F11>", lambda e: self.attributes("-fullscreen", not self.attributes("-fullscreen")))
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))

        ctk.CTkLabel(self, text="⚽ Chat Fútbol",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=15)

        self.chat = ctk.CTkTextbox(self, height=450, font=ctk.CTkFont(size=13), wrap="word")
        self.chat.pack(pady=5, padx=20, fill="both", expand=True)
        self.chat.configure(state="disabled")
        # Tags visuales: nombre del hablante con color diferente
        self.chat.tag_config("bot_label",  foreground="#4FC3F7")
        self.chat.tag_config("user_label", foreground="#AAAAAA")

        self.label_status = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.label_status.pack()

        frame_input = ctk.CTkFrame(self, fg_color="transparent")
        frame_input.pack(pady=10, padx=20, fill="x")

        self.input = ctk.CTkTextbox(frame_input, height=40, font=ctk.CTkFont(size=13))
        self.input.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.input.bind("<Return>", lambda e: self.enviar() or "break")

        self.btn_enviar = ctk.CTkButton(frame_input, text="Enviar", width=90, height=40,
                                        command=self.enviar)
        self.btn_enviar.pack(side="right")

        self.agregar_mensaje("🤖", "¡Buenas! ¿De qué hablamos? Puedo charlar de fútbol, darte datos, o analizar un partido.")

    def agregar_mensaje(self, quien, texto):
        self.chat.configure(state="normal")
        tag = "bot_label" if quien == "🤖" else "user_label"
        self.chat.insert("end", f"\n{quien}", tag)
        self.chat.insert("end", f"\n{texto}\n\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def set_status(self, texto):
        self.label_status.configure(text=texto)
        self.update()

    def enviar(self):
        mensaje = self.input.get("1.0", "end").strip()
        if not mensaje:
            return
        self.input.delete("1.0", "end")
        self.agregar_mensaje("👤", mensaje)
        self.btn_enviar.configure(state="disabled")
        self.input.configure(state="disabled")
        thread = threading.Thread(target=self.procesar, args=(mensaje,))
        thread.daemon = True
        thread.start()

    def procesar(self, mensaje):
        global LIGA_ID, TEMPORADA_ID, RONDAS_TOTALES
        try:
            self.set_status("💬 Pensando...")

            # ── Paso 1: obtener respuesta COMPLETA sin mostrar nada ───────────
            # Detectar ANTES si es un pedido de predicción/apuesta.
            # Si lo es, inyectamos un recordatorio urgente al LLM para que
            # dispare ACTION:ANALIZAR en vez de inventar estadísticas.
            # Detectar si el usuario responde a una pregunta de aclaración previa
            # (ej: bot preguntó "¿De qué partido hablás?" y el usuario dice "el de KR")
            es_confirmacion = _es_respuesta_a_aclaracion_partido()
            es_pred = _es_prediccion(mensaje) or es_confirmacion
            es_schedule = _es_consulta_schedule(mensaje)

            # ── Búsqueda directa de fixtures en SofaScore (sin pasar por el LLM) ──
            # Para consultas de horario/rival siempre vamos directo a SofaScore:
            # así la respuesta SIEMPRE incluye rival + hora real, sin depender del LLM.
            if es_schedule and not es_pred:
                equipo_extraido = _extraer_equipo_schedule(mensaje)
                # Si el usuario no mencionó equipo (ej: "A que hora"), buscarlo en historial
                if not equipo_extraido:
                    equipo_extraido = _extraer_equipo_de_historial()
                if equipo_extraido:
                    historial.append({"role": "user", "content": mensaje})
                    self.set_status(f"🔍 Buscando partidos de {equipo_extraido} en SofaScore...")
                    partidos_encontrados = buscar_fixture_equipo(equipo_extraido)
                    if not partidos_encontrados:
                        # Fallback: buscar en los fixtures cargados al inicio
                        # (cubre ligas pequeñas que no aparecen en el endpoint global)
                        partidos_encontrados = _buscar_en_fixtures_cargados(equipo_extraido)
                    if partidos_encontrados:
                        # Mostrar solo el partido más próximo (el primero en orden cronológico)
                        f          = partidos_encontrados[0]
                        es_hoy_f   = "[HOY]"      in f
                        en_curso_f = "[EN CURSO]" in f
                        f_limpio   = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', ' ', f).strip()
                        sufijo     = " (hoy)"       if es_hoy_f   else ""
                        sufijo    += " — en curso"  if en_curso_f else ""
                        texto = f"El próximo partido de {equipo_extraido} es:\n• {f_limpio}{sufijo}"
                    else:
                        texto = f"No encontré próximos partidos de {equipo_extraido} en SofaScore."
                    historial.append({"role": "assistant", "content": texto})
                    self.agregar_mensaje("🤖", texto)
                    return  # finally re-habilita los botones

            respuesta = chat_con_ia(mensaje, forzar_action=es_pred,
                                    es_confirmacion_partido=es_confirmacion,
                                    forzar_fixtures=es_schedule and not es_pred)
            # Limpiar tags internas que nunca deben llegar al usuario
            respuesta = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', ' ', respuesta).strip()

            # ── Caso 0: la respuesta contiene ACTION:BUSCAR_FIXTURE ──────────
            if "ACTION:BUSCAR_FIXTURE|" in respuesta:
                match_fix = re.search(r'ACTION:BUSCAR_FIXTURE\|(.*?)(?:\n|$)', respuesta)
                equipo_buscar = match_fix.group(1).strip() if match_fix else None
                if equipo_buscar:
                    self.set_status(f"🔍 Buscando partidos de {equipo_buscar} en SofaScore...")
                    fixtures = buscar_fixture_equipo(equipo_buscar)
                    if not fixtures:
                        fixtures = _buscar_en_fixtures_cargados(equipo_buscar)
                    if fixtures:
                        # Mostrar solo el partido más próximo
                        f            = fixtures[0]
                        es_hoy_fix   = "[HOY]"      in f
                        en_curso_fix = "[EN CURSO]" in f
                        f_limpio     = re.sub(r'\s*\[HOY\]\s*|\s*\[EN CURSO\]\s*', ' ', f).strip()
                        sufijo       = " (hoy)"       if es_hoy_fix   else ""
                        sufijo      += " — en curso"  if en_curso_fix else ""
                        texto = f"El próximo partido de {equipo_buscar} es:\n• {f_limpio}{sufijo}"
                    else:
                        texto = f"No encontré próximos partidos de {equipo_buscar} en SofaScore."
                    # Limpiar historial para no recordar la ACTION
                    if historial and historial[-1]["role"] == "assistant":
                        historial[-1]["content"] = texto
                    self.agregar_mensaje("🤖", texto)
                self.set_status("")
                return

            # ── Caso A': ACTION:COMBINADA_AUTO[|liga_filtro] ─────────────────
            if "ACTION:COMBINADA_AUTO" in respuesta:
                # Parsear liga opcional: ACTION:COMBINADA_AUTO|Copa Libertadores
                m_auto = re.search(r'ACTION:COMBINADA_AUTO(?:\|([^\n|]+))?', respuesta)
                liga_filtro_auto = m_auto.group(1).strip() if (m_auto and m_auto.group(1)) else ""
                status_msg = (f"🔍 Buscando combinada de {liga_filtro_auto}..."
                              if liga_filtro_auto else "🔍 Buscando la mejor combinada en los fixtures...")
                self.set_status(status_msg)
                picks, debug_info = hacer_combinada_auto(
                    n_picks=2,
                    progress_cb=self.set_status,
                    liga_filtro=liga_filtro_auto,
                )
                texto = _formatear_combinada(picks, liga_filtro=liga_filtro_auto,
                                             debug_info=debug_info)
                if picks:
                    _guardar_picks_combinada(picks)
                if historial and historial[-1]["role"] == "assistant":
                    historial[-1]["content"] = texto
                self.agregar_mensaje("🤖", texto)
                self.set_status("✅ Listo")
                return

            # ── Caso A'': ACTION:COMBINADA|... (específica o multi-partido) ────
            if "ACTION:COMBINADA|" in respuesta:
                m_comb = re.search(r'ACTION:COMBINADA\|(.*?)(?:\n|$)', respuesta)
                if m_comb:
                    raw = m_comb.group(1).strip()
                    partidos_picks = []
                    for pick_str in raw.split(";"):
                        partes = [p.strip() for p in pick_str.split("|")]
                        if len(partes) >= 4:
                            eq1        = partes[0]
                            eq2        = partes[1]
                            stats_raw  = partes[2]
                            liga_n     = partes[3]
                            stats_list = (
                                ["auto"] if stats_raw.lower() == "auto"
                                else [s.strip() for s in stats_raw.split(",")]
                            )
                            partidos_picks.append((eq1, eq2, stats_list, liga_n))

                    if partidos_picks:
                        n_total = len(partidos_picks)
                        self.set_status(
                            f"🔄 Analizando combinada ({n_total} partido"
                            f"{'s' if n_total > 1 else ''})..."
                        )
                        picks = hacer_combinada_especifica(partidos_picks)
                        texto = _formatear_combinada(picks)
                        if picks:
                            _guardar_picks_combinada(picks)
                        if historial and historial[-1]["role"] == "assistant":
                            historial[-1]["content"] = texto
                        self.agregar_mensaje("🤖", texto)
                        self.set_status("✅ Listo")
                        return

            # ── Caso A: la respuesta contiene ACTION:ANALIZAR ─────────────────
            if "ACTION:ANALIZAR|" in respuesta:
                equipo1 = equipo2 = foco = liga_nombre = None
                match = re.search(r'ACTION:ANALIZAR\|(.*?)\|(.*?)\|(.*?)\|(.*?)(?:\n|$)', respuesta)
                if match:
                    equipo1     = match.group(1).strip()
                    equipo2     = match.group(2).strip()
                    foco        = match.group(3).strip()
                    liga_nombre = match.group(4).strip()
                else:
                    partes      = respuesta.split("ACTION:ANALIZAR|")[1].split("|")
                    equipo1     = partes[0].strip() if len(partes) > 0 else None
                    equipo2     = partes[1].strip() if len(partes) > 1 else None
                    foco        = partes[2].strip() if len(partes) > 2 else "completo"
                    liga_nombre = partes[3].strip() if len(partes) > 3 else "Besta deild karla"

                if not equipo1 or not equipo2:
                    self.set_status("")
                    return

                # Guardia: al menos uno de los equipos debe haber sido mencionado
                # por el usuario en la conversación. Si ninguno aparece, el LLM
                # inventó el partido — rechazamos antes de llamar a SofaScore.
                historial_usuario = " ".join(
                    m["content"].lower() for m in historial if m["role"] == "user"
                )
                eq1_word = equipo1.lower().split()[0]
                eq2_word = equipo2.lower().split()[0]
                if eq1_word not in historial_usuario and eq2_word not in historial_usuario:
                    texto_err = (
                        "No pude identificar de qué partido me hablás. "
                        "¿Podés decirme los dos equipos que querés analizar?"
                    )
                    if historial and historial[-1]["role"] == "assistant":
                        historial[-1]["content"] = texto_err
                    self.agregar_mensaje("🤖", texto_err)
                    self.set_status("")
                    return

                liga = next((v for k, v in LIGAS.items() if liga_nombre in k), None)
                if not liga:
                    liga = {"id": 188, "temporada": 89094, "rondas": 7}
                LIGA_ID        = liga["id"]
                TEMPORADA_ID   = liga["temporada"]
                RONDAS_TOTALES = liga["rondas"]

                foco_lower = foco.lower()
                self.set_status("🔄 Bajando datos de SofaScore...")

                # ── Rama especial: corners antes del minuto X ──────────────
                if foco_lower.startswith("corners_antes_"):
                    try:
                        minuto_x = int(foco_lower.replace("corners_antes_", ""))
                    except ValueError:
                        minuto_x = 45
                    datos, lineas_py, prom_eq1, prom_eq2 = hacer_analisis_corners_tiempo(
                        equipo1, equipo2, minuto_x
                    )
                    evento_id = None
                    info_ronda = ""
                else:
                    datos, evento_id, info_ronda, lineas_py, prom_eq1, prom_eq2 = hacer_analisis_completo(equipo1, equipo2)

                print("=== DATOS SOFASCORE ===")
                print(datos)
                print("======================")

                self.set_status("🤖 Analizando...")
                # Instrucción específica para el foco pedido
                instruccion_foco = _FOCO_PROMPT.get(foco_lower, _FOCO_PROMPT["completo"])

                # ── Contexto competitivo ────────────────────────────────────
                _COPAS = {"Champions League", "Copa Libertadores", "Copa Sudamericana"}
                ctx_comp = f"COMPETICIÓN: {liga_nombre}"
                if info_ronda:
                    ctx_comp += f" — {info_ronda}"

                if liga_nombre in _COPAS:
                    nota_comp = (
                        "Este partido es de una copa eliminatoria o por fases de grupos. "
                        "Considerá si algún equipo podría estar jugándose la clasificación "
                        "o enfrentando la eliminación: eso suele elevar la intensidad "
                        "(más faltas y tarjetas por presión, más corners defensivos, "
                        "o más goles si un equipo necesita marcar sí o sí). "
                        "Mencioná brevemente este factor y si puede ajustar la línea recomendada al alza."
                    )
                else:
                    nota_comp = (
                        "Considerá si el partido tiene relevancia especial en la tabla "
                        "(lucha por el título, zona de clasificación a copas, pelea por el descenso), "
                        "ya que eso puede afectar la intensidad y las stats esperadas."
                    )

                # ── Generar párrafos 1 y 2 en Python (100% confiable) ─────────
                parrafos_python = _generar_parrafos_python(
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
                        f"Analizá el partido {equipo1} vs {equipo2}.\n{ctx_comp}\n{instruccion_foco}"
                    )

                analisis = chat_con_ia(prompt_analisis, datos_sofascore=datos)

                analisis_limpio = re.sub(r'ACTION:ANALIZAR\|[^\n]+', '', analisis).strip()

                # Cabecera de confirmación: muestra partido + liga antes del análisis
                foco_label = {
                    "corners": "Corners", "corners_1h": "Corners 1T", "corners_2h": "Corners 2T",
                    "goles": "Goles", "tarjetas_amarillas": "Tarjetas amarillas",
                    "tarjetas_amarillas_1h": "Tarjetas AM 1T", "tarjetas_amarillas_2h": "Tarjetas AM 2T",
                    "tarjetas_rojas": "Tarjetas rojas", "remates": "Remates al arco",
                    "faltas": "Faltas", "faltas_1h": "Faltas 1T", "faltas_2h": "Faltas 2T",
                    "completo": "Análisis completo",
                }.get(foco, foco)
                encabezado = f"⚽ {equipo1} vs {equipo2}"
                if liga_nombre:
                    encabezado += f"  •  {liga_nombre}"
                if info_ronda:
                    encabezado += f"  •  {info_ronda}"
                encabezado += f"\n📌 Foco: {foco_label}\n"
                encabezado += "─" * 40 + "\n\n"

                self.agregar_mensaje("🤖", encabezado + analisis_limpio)
                guardar_prediccion(equipo1, equipo2, foco, analisis_limpio,
                                   evento_id=evento_id, liga_id=LIGA_ID, temporada_id=TEMPORADA_ID)
                self.set_status("✅ Listo")

            # ── Caso B: respuesta normal ──────────────────────────────────────
            else:
                # Guardia de seguridad: si era un pedido de predicción y el LLM
                # NO disparó ACTION:ANALIZAR, descartar la respuesta SALVO que
                # sea una pregunta de aclaración legítima (tiene "?" y es corta).
                if es_pred:
                    # Permitir respuestas con "?" que sean preguntas de aclaración
                    # (ej: "¿De qué partido hablás? Valur tiene KR [HOY] y Víkingur el 31/05.")
                    # Aumentamos el límite a 400 chars para cubrir listas de partidos con fechas.
                    # Una aclaración legítima tiene "?", es corta, y NO contiene
                    # patrones de estadísticas inventadas (promedio X, Over X.Y, etc.)
                    es_aclaracion = (
                        "?" in respuesta
                        and len(respuesta.strip()) < 400
                        and not _STATS_INVENTADAS.search(respuesta)
                    )
                    if not es_aclaracion:
                        texto_limpio = _MSG_SIN_DATOS
                        # Corregir también el historial para no "recordar" stats falsas
                        if historial and historial[-1]["role"] == "assistant":
                            historial[-1]["content"] = texto_limpio
                        self.agregar_mensaje("🤖", texto_limpio)
                        self.set_status("")
                        return

                self.chat.configure(state="normal")
                self.chat.insert("end", "\n🤖:\n")
                self.chat.configure(state="disabled")

                # Reproducir la respuesta en trozos pequeños para imitar el
                # efecto de streaming real (4 chars cada ~12 ms ≈ ritmo natural).
                CHUNK = 4
                for i in range(0, len(respuesta), CHUNK):
                    trozo = respuesta[i:i + CHUNK]
                    self.chat.configure(state="normal")
                    self.chat.insert("end", trozo)
                    self.chat.see("end")
                    self.chat.configure(state="disabled")
                    self.update()
                    time.sleep(0.012)

                self.chat.configure(state="normal")
                self.chat.insert("end", "\n")
                self.chat.configure(state="disabled")
                self.set_status("")

        except Exception as e:
            self.agregar_mensaje("❌ Error", str(e))
            self.set_status("")
        finally:
            self.btn_enviar.configure(state="normal")
            self.input.configure(state="normal")


if __name__ == "__main__":
    print("🔄 Verificando predicciones anteriores...")
    verificar_predicciones(_nueva_sesion())

    print("🔄 Cargando fixtures...")
    fixtures = cargar_proximos_partidos()
    import fixture_loader
    LIGAS.update(fixture_loader.LIGAS)
    print("✅ Fixtures cargados")
    app = App(fixtures)
    app.mainloop()