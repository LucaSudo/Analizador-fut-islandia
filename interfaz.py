from dotenv import load_dotenv
load_dotenv()
from fixture_loader import cargar_proximos_partidos
import os
import re
import time
import threading
from datetime import datetime
import customtkinter as ctk
from groq import Groq
from playwright.sync_api import sync_playwright
from memory import cargar_memoria, guardar_prediccion, generar_contexto_memoria
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

def fetch_api(page, url):
    return page.evaluate(f"""
        async () => {{
            const r = await fetch('{url}');
            return await r.json();
        }}
    """)

def obtener_pagina():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    page.goto("https://www.sofascore.com", timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    return playwright, browser, page

MAX_DIAS_HISTORIAL = 60   # no usar partidos de más de 60 días de antigüedad

def obtener_partidos_equipo(page, nombre_equipo, ultimas_rondas=5):
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
        data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/events/round/{ronda}")
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

def obtener_estadisticas(page, evento_id):
    """Devuelve dict {periodo_stat: {home, away}} para un evento."""
    try:
        data = fetch_api(page, f"https://www.sofascore.com/api/v1/event/{evento_id}/statistics")
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

import math as _math

def calcular_linea_over(total_esperado: float) -> str:
    """
    Devuelve la línea Over en formato X.5 inmediatamente MENOR al total esperado.
    Regla: parte_entera(total) + 0.5
    Ejemplos: 13.80 → 'Over 13.5' | 10.20 → 'Over 9.5' | 9.70 → 'Over 9.5'
    """
    base = int(total_esperado)   # parte entera → piso natural
    linea = base + 0.5
    # Verificar que la línea es efectivamente menor al total
    if linea >= total_esperado:
        linea -= 1.0
    return f"Over {linea:.1f}"

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

def precomputar_stats_equipo(page, nombre_equipo, n=5):
    """
    Busca los últimos N partidos terminados y calcula promedios EN PYTHON,
    extrayendo el valor correcto (local o visitante) de cada stat.
    Así el LLM recibe promedios ya calculados, sin riesgo de confundir columnas.
    """
    partidos = obtener_partidos_equipo(page, nombre_equipo, n)
    acum     = {f"{p}_{s}": [] for p, s in _STATS_A_PRECOMPUTAR}
    goles    = []
    refs     = []

    for e in partidos:
        home     = e["homeTeam"]["name"]
        away     = e["awayTeam"]["name"]
        es_local = nombre_equipo.lower() in home.lower()
        ronda    = e.get("roundInfo", {}).get("round", "?")

        gh = e.get("homeScore", {}).get("current", None)
        ga = e.get("awayScore", {}).get("current", None)
        if gh is not None and ga is not None:
            goles.append(gh if es_local else ga)

        fecha_str = (
            datetime.fromtimestamp(e["startTimestamp"]).strftime("%d/%m/%Y")
            if e.get("startTimestamp") else "?"
        )
        refs.append(
            f"{fecha_str} R{ronda}: {home} {gh}-{ga} {away} "
            f"({'local' if es_local else 'visitante'})"
        )

        stats = obtener_estadisticas(page, e["id"])
        page.wait_for_timeout(800)   # evitar rate-limiting de SofaScore

        for periodo, stat_name in _STATS_A_PRECOMPUTAR:
            clave   = f"{periodo}_{stat_name}"
            if clave not in stats:
                continue
            val_str = stats[clave]["home"] if es_local else stats[clave]["away"]
            try:
                acum[clave].append(int(str(val_str)))
            except (ValueError, TypeError):
                pass

    lineas = [f"ESTADÍSTICAS DE {nombre_equipo.upper()} (últimos {len(partidos)} partidos terminados):"]
    lineas.append(f"  Partidos: {' | '.join(refs)}")

    if goles:
        lineas.append(f"  Goles anotados: {goles} → promedio = {sum(goles)/len(goles):.2f}")

    for periodo, stat_name in _STATS_A_PRECOMPUTAR:
        clave  = f"{periodo}_{stat_name}"
        vals   = acum[clave]
        if vals:
            prom = sum(vals) / len(vals)
            lineas.append(f"  {clave}: {vals} → promedio = {prom:.2f}")

    promedios = {}
    if goles:
        promedios["goles"] = sum(goles) / len(goles)
    for periodo, stat_name in _STATS_A_PRECOMPUTAR:
        clave = f"{periodo}_{stat_name}"
        if acum[clave]:
            promedios[clave] = sum(acum[clave]) / len(acum[clave])

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

def hacer_analisis_completo(equipo1, equipo2):
    global RONDAS_TOTALES
    playwright, browser, page = obtener_pagina()
    try:
        # Actualizar RONDAS_TOTALES a la ronda real antes de buscar partidos
        try:
            rounds_data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/rounds")
            rondas_list = rounds_data.get("rounds", [])
            # currentRound.round = ronda en curso; fallback a última ronda del array
            RONDAS_TOTALES = (
                rounds_data.get("currentRound", {}).get("round")
                or (rondas_list[-1].get("round", RONDAS_TOTALES) if rondas_list else RONDAS_TOTALES)
            )
        except:
            pass  # Si falla, usar el valor que ya tiene RONDAS_TOTALES

        # Pre-calcular stats en Python para cada equipo.
        # Devuelve (texto, dict_promedios) para calcular la línea de apuesta en Python.
        stats_eq1, promedios_eq1 = precomputar_stats_equipo(page, equipo1)
        stats_eq2, promedios_eq2 = precomputar_stats_equipo(page, equipo2)

        # Buscar el próximo partido entre los dos equipos (incluye hoy aunque
        # el timestamp ya pasó — mismo criterio que fixture_loader)
        ahora = datetime.now().timestamp()
        from datetime import date
        inicio_hoy = datetime.combine(date.today(), datetime.min.time()).timestamp()
        evento_id_proximo = None
        ronda_inicio = max(1, RONDAS_TOTALES - 1)
        for ronda in range(ronda_inicio, RONDAS_TOTALES + 7):
            data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/events/round/{ronda}")
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
                    or (status == "notstarted" and es_hoy)  # partido de hoy aún sin actualizar
                )
                if equipo1_match and equipo2_match and es_vigente:
                    evento_id_proximo = evento["id"]
                    break
            if evento_id_proximo:
                break

    finally:
        browser.close()
        playwright.stop()

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
        if v1 is not None and v2 is not None:
            total = v1 + v2
            lineas_python[foco_key] = (total, calcular_linea_over(total))

    # Agregar al contexto las líneas ya calculadas
    lineas_ctx = []
    for foco_key, (total, linea) in lineas_python.items():
        lineas_ctx.append(f"  {foco_key}: total esperado = {total:.2f} → línea Python = {linea}")

    contexto = (
        "DATOS REALES DE SOFASCORE (promedios ya calculados por equipo):\n\n"
        f"{stats_eq1}\n\n"
        f"{stats_eq2}\n\n"
        "LÍNEAS DE APUESTA PRE-CALCULADAS POR PYTHON (usar directamente, no recalcular):\n"
        + ("\n".join(lineas_ctx) if lineas_ctx else "  (sin datos suficientes)")
        + "\n"
    )
    return contexto, evento_id_proximo


# ── Chat con IA ──────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos un asistente especializado en fútbol. Tu única función es responder preguntas de fútbol y, cuando se te pida explícitamente, analizar partidos usando ACTION:ANALIZAR.

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
  - "¿Cuándo juega X?" → respondés solo la fecha y hora. Nada más.
  - "¿A qué hora juega X?" → respondés solo la hora. Nada más.
  - "¿Cuál es el próximo partido de X?" → nombrás el partido. Nada más.
  - "¿Dónde juega X?" → respondés el estadio o ciudad. Nada más.
  - "¿Quién es el goleador de X?" → respondés el dato. Nada más.
  - "Contame sobre el equipo X" → información general. Nada más.
  - "¿Viste que hoy juega X?" → confirmás el partido y el horario. Nada más.
  - "¿Sabés que juega X hoy?" → confirmás el partido y el horario. Nada más.
  - "¿Viste que juega X contra Y?" → confirmás el partido. Nada más.
  - Cualquier pregunta informativa, histórica o general → respondés directo. Nada más.

NINGUNO de estos casos activa ACTION:ANALIZAR, aunque mencionen un partido, un equipo o un resultado. Que el usuario mencione un partido NO es un pedido de análisis.

════════════════════════════════════════
REGLA ABSOLUTA N°3 — CONFIRMACIÓN DE PARTIDO ANTES DE ANALIZAR
════════════════════════════════════════

Antes de disparar ACTION:ANALIZAR, necesitás tener CLARO de qué partido específico se habla.

CASO A — El usuario YA es específico (nombró los dos equipos, dijo "de hoy" y hay un partido [HOY] del equipo, o el contexto no deja lugar a dudas):
→ NO necesitás confirmar. Identificás el partido y disparás ACTION:ANALIZAR directamente.
→ Ejemplo: "cuántos corners habrá en el partido de hoy del Valur" → el partido [HOY] es el de hoy → disparás directo.

CASO B — El usuario menciona un equipo sin especificar cuál partido, y ese equipo tiene MÁS DE UN partido próximo en tu lista:
→ Preguntás: "¿De qué partido hablás? [Equipo] tiene [partido1 con fecha] y [partido2 con fecha]."
→ NO disparás ACTION:ANALIZAR hasta que el usuario especifique.

CASO C — El usuario menciona un equipo sin especificar, y ese equipo tiene UN SOLO partido próximo:
→ Confirmás brevemente: "Entiendo que hablás del partido contra [rival] el [fecha], ¿es correcto?"
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
  3. La liga debe ser exactamente uno de estos valores (copiado tal cual, sin variaciones):
       - Besta deild karla
       - La Liga
       - Premier League
       - Serie A
       - Bundesliga
       - Ligue 1
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
  - Besta deild karla (Islandia)
  - La Liga (España)
  - Premier League (Inglaterra)
  - Serie A (Italia)
  - Bundesliga (Alemania)
  - Ligue 1 (Francia)
  - Champions League
  - Liga Argentina
  - Copa Libertadores (Sudamérica)
  - Copa Sudamericana (Sudamérica)
  - Saudi Pro League (Arabia Saudita)
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
]
# "cuántos/cuántas" solo es predicción cuando va seguido de una stat de partido
_PRED_STAT_RE = re.compile(
    r'cu[aá]nt[oa]s?\s+(goles?|corners?|tarjetas?|amarillas?|rojas?|faltas?|remates?|tiros?)',
    re.IGNORECASE
)

def _es_prediccion(msg: str) -> bool:
    m = msg.lower()
    return any(kw in m for kw in _PRED_KEYWORDS) or bool(_PRED_STAT_RE.search(msg))

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
    r'promedio\s+de\s+\d|'          # "promedio de 6"
    r'\(\s*\d+\s*\+\s*\d+|'         # "(3+2+..."
    r'/\s*\d+\s*=\s*\d|'            # "/ 10 = 3"
    r'recomendaci[oó]n:\s*.{0,60}\d',  # "Recomendación: Over 2.5"
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
_FOCO_PROMPT = {
    "completo": (
        "Hacé un resumen con las stats principales: "
        "mencioná el promedio de goles de cada equipo (y si suelen anotar ambos), "
        "el total de corners esperado, tarjetas amarillas esperadas y faltas. "
        "Para cada stat leé el 'promedio' de la sección ESTADÍSTICAS y sumá los dos. "
        "Usá las líneas de LÍNEAS PRE-CALCULADAS para las recomendaciones."
    ),
    "goles": (
        "Leé el promedio de 'Goles anotados' de cada equipo en la sección ESTADÍSTICAS. "
        "Mostrá: equipo1 promedio X, equipo2 promedio Y, total X+Y. "
        "Indicá también si suelen anotar ambos. "
        "Usá la línea 'goles' de LÍNEAS PRE-CALCULADAS."
    ),
    "corners": (
        "Leé el promedio de ALL_Corner kicks de cada equipo en la sección ESTADÍSTICAS. "
        "Mostrá: equipo1 promedio X, equipo2 promedio Y, total X+Y. "
        "Usá la línea 'corners' de LÍNEAS PRE-CALCULADAS."
    ),
    "corners_1h": (
        "Leé el promedio de 1ST_Corner kicks de cada equipo en ESTADÍSTICAS. "
        "Mostrá los promedios individuales y el total (suma). "
        "Usá la línea 'corners_1h' de LÍNEAS PRE-CALCULADAS."
    ),
    "corners_2h": (
        "Leé el promedio de 2ND_Corner kicks de cada equipo en ESTADÍSTICAS. "
        "Mostrá los promedios individuales y el total (suma). "
        "Usá la línea 'corners_2h' de LÍNEAS PRE-CALCULADAS."
    ),
    "tarjetas_amarillas": (
        "Leé el promedio de ALL_Yellow cards de cada equipo en ESTADÍSTICAS. "
        "Mostrá los promedios individuales y el total (suma). "
        "Usá la línea 'tarjetas_amarillas' de LÍNEAS PRE-CALCULADAS."
    ),
    "tarjetas_amarillas_1h": (
        "Leé el promedio de 1ST_Yellow cards de cada equipo en ESTADÍSTICAS. "
        "Mostrá los promedios individuales y el total (suma). "
        "Usá la línea 'tarjetas_amarillas_1h' de LÍNEAS PRE-CALCULADAS."
    ),
    "tarjetas_amarillas_2h": (
        "Leé el promedio de 2ND_Yellow cards de cada equipo en ESTADÍSTICAS. "
        "Mostrá los promedios individuales y el total (suma). "
        "Usá la línea 'tarjetas_amarillas_2h' de LÍNEAS PRE-CALCULADAS."
    ),
    "tarjetas_rojas": (
        "Leé el promedio de ALL_Red cards de cada equipo en ESTADÍSTICAS. "
        "Indicá en cuántos partidos hubo roja y si es probable. "
        "Usá la línea 'tarjetas_rojas' de LÍNEAS PRE-CALCULADAS si existe; "
        "si no, recomendá Sí/No según la frecuencia."
    ),
    "tarjetas_rojas_1h": (
        "Leé el promedio de 1ST_Red cards. Frecuencia de rojas en 1er tiempo. "
        "Recomendá Sí/No según la frecuencia observada."
    ),
    "tarjetas_rojas_2h": (
        "Leé el promedio de 2ND_Red cards. Frecuencia de rojas en 2do tiempo. "
        "Recomendá Sí/No según la frecuencia observada."
    ),
    "remates": (
        "Leé el promedio de ALL_Shots on target de cada equipo en ESTADÍSTICAS. "
        "Mostrá los promedios individuales y el total (suma). "
        "Usá la línea 'remates' de LÍNEAS PRE-CALCULADAS."
    ),
    "remates_1h": (
        "Leé el promedio de 1ST_Shots on target. Mostrá promedios y total. "
        "Usá la línea 'remates_1h' de LÍNEAS PRE-CALCULADAS."
    ),
    "remates_2h": (
        "Leé el promedio de 2ND_Shots on target. Mostrá promedios y total. "
        "Usá la línea 'remates_2h' de LÍNEAS PRE-CALCULADAS."
    ),
    "faltas": (
        "Leé el promedio de ALL_Fouls de cada equipo en ESTADÍSTICAS. "
        "Mostrá los promedios individuales y el total (suma). "
        "Usá la línea 'faltas' de LÍNEAS PRE-CALCULADAS."
    ),
    "faltas_1h": (
        "Leé el promedio de 1ST_Fouls. Mostrá promedios y total. "
        "Usá la línea 'faltas_1h' de LÍNEAS PRE-CALCULADAS."
    ),
    "faltas_2h": (
        "Leé el promedio de 2ND_Fouls. Mostrá promedios y total. "
        "Usá la línea 'faltas_2h' de LÍNEAS PRE-CALCULADAS."
    ),
}

def chat_con_ia(mensaje, datos_sofascore=None, callback=None, forzar_action=False,
                es_confirmacion_partido=False):
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

    if forzar_action and historial:
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
            inyeccion = (
                "⚠️ ACCIÓN REQUERIDA — El usuario pide una PREDICCIÓN o ESTADÍSTICA. "
                "Seguí EXACTAMENTE estas reglas:\n\n"
                "CASO 1 — El partido es ABSOLUTAMENTE CLARO:\n"
                "  ÚNICAMENTE si el usuario nombró AMBOS equipos explícitamente en su mensaje,\n"
                "  O dijo 'de hoy'/'hoy' y hay EXACTAMENTE un partido [HOY] de ese equipo.\n"
                "  → Identificás ese partido, escribís UNA frase corta y terminás con:\n"
                "  ACTION:ANALIZAR|equipo_local|equipo_visitante|foco|liga\n\n"
                "CASO 2 — CUALQUIER OTRA SITUACIÓN (usuario mencionó solo UN equipo sin\n"
                "  aclarar cuál partido, no dijo 'hoy', o hay varios partidos próximos):\n"
                "  → Buscás en los fixtures los partidos próximos de ese equipo y preguntás:\n"
                "    Si tiene 1 partido: '¿Hablás del partido [Equipo] vs [Rival] el [fecha]?'\n"
                "    Si tiene 2+ partidos: '¿De qué partido hablás? [Equipo] tiene [partido1 fecha] y [partido2 fecha].'\n"
                "  NO emitas ACTION:ANALIZAR. Esperá la respuesta del usuario.\n\n"
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
        self.chat.insert("end", f"\n{quien}:\n{texto}\n")
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
            respuesta = chat_con_ia(mensaje, forzar_action=es_pred,
                                    es_confirmacion_partido=es_confirmacion)

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

                liga = next((v for k, v in LIGAS.items() if liga_nombre in k), None)
                if not liga:
                    liga = {"id": 188, "temporada": 89094, "rondas": 7}
                LIGA_ID        = liga["id"]
                TEMPORADA_ID   = liga["temporada"]
                RONDAS_TOTALES = liga["rondas"]

                self.set_status("🔄 Bajando datos de SofaScore...")
                datos, evento_id = hacer_analisis_completo(equipo1, equipo2)

                print("=== DATOS SOFASCORE ===")
                print(datos)
                print("======================")

                self.set_status("🤖 Analizando...")
                # Detectar si el foco es por período
                foco_lower = foco.lower()
                if foco_lower.endswith("_1h"):
                    instruccion_periodo = "Usá ÚNICAMENTE los datos con prefijo 1ST_ (primer tiempo). NUNCA uses ALL_ ni 2ND_."
                elif foco_lower.endswith("_2h"):
                    instruccion_periodo = "Usá ÚNICAMENTE los datos con prefijo 2ND_ (segundo tiempo). NUNCA uses ALL_ ni 1ST_."
                else:
                    instruccion_periodo = "Usá los datos con prefijo ALL_ (partido completo)."

                # Instrucción específica para el foco pedido
                instruccion_foco = _FOCO_PROMPT.get(foco_lower, _FOCO_PROMPT["completo"])

                analisis = chat_con_ia(
                f"""Analizá el partido {equipo1} vs {equipo2} usando los datos de SofaScore.

PERÍODO A USAR: {instruccion_periodo}

IMPORTANTE: Los datos ya incluyen los promedios pre-calculados por equipo.
Cada línea tiene el formato: "stat: [v1, v2, ...] → promedio = X.XX"
Los promedios son exactos — usá esos valores directamente, no los recalcules.

TAREA:
{instruccion_foco}

REGLAS:
- Usá SOLO los promedios del período indicado (no mezcles ALL/1ST/2ND).
- Total del partido = promedio_equipo1 + promedio_equipo2 (SUMÁ, no promedies).
- LÍNEA DE APUESTA: usá EXACTAMENTE la línea que figura en "LÍNEAS DE APUESTA
  PRE-CALCULADAS POR PYTHON" para el foco correspondiente. No la recalcules.
  Esa línea garantiza que X.5 < total esperado (apuesta con sentido estadístico).
- Si hay menos de 4 partidos con datos, aclaralo.
- Máximo 120 palabras. Texto corrido, sin listas.
- NO uses conocimiento propio.
- Terminá con: "⚠️ Solo una recomendación estadística. Los resultados pueden variar." """,
                datos_sofascore=datos
            )
                

                analisis_limpio = re.sub(r'ACTION:ANALIZAR\|[^\n]+', '', analisis).strip()
                self.agregar_mensaje("🤖", analisis_limpio)
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
                    es_aclaracion = "?" in respuesta and len(respuesta.strip()) < 400
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
    from playwright.sync_api import sync_playwright
    print("🔄 Verificando predicciones anteriores...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0")
        page = context.new_page()
        page.goto("https://www.sofascore.com", timeout=30000, wait_until="domcontentloaded")
        verificar_predicciones(page)
        browser.close()

    print("🔄 Cargando fixtures...")
    fixtures = cargar_proximos_partidos()
    import fixture_loader
    LIGAS.update(fixture_loader.LIGAS)
    print("✅ Fixtures cargados")
    app = App(fixtures)
    app.mainloop()