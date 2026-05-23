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

def obtener_partidos_equipo(page, nombre_equipo, ultimas_rondas=4):
    partidos = []
    for ronda in range(RONDAS_TOTALES, max(0, RONDAS_TOTALES - ultimas_rondas - 2), -1):
        data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/events/round/{ronda}")
        for evento in data.get("events", []):
            home = evento["homeTeam"]["name"]
            away = evento["awayTeam"]["name"]
            if nombre_equipo.lower() in home.lower() or nombre_equipo.lower() in away.lower():
                partidos.append(evento)
        if len(partidos) >= ultimas_rondas:
            break
    return partidos[:ultimas_rondas]

def obtener_estadisticas(page, evento_id):
    try:
        data = fetch_api(page, f"https://www.sofascore.com/api/v1/event/{evento_id}/statistics")
        stats = {}
        for grupo in data.get("statistics", []):
            periodo = grupo["period"]  # "ALL", "1ST" o "2ND"
            for g in grupo["groups"]:
                for item in g["statisticsItems"]:
                    stats[f"{periodo}_{item['name']}"] = {
                        "home": item.get("home", "?"),
                        "away": item.get("away", "?")
                    }
        return stats
    except:
        return {}

def formatear_partido(evento, stats):
    home = evento["homeTeam"]["name"]
    away = evento["awayTeam"]["name"]
    gh = evento.get("homeScore", {}).get("current", "?")
    ga = evento.get("awayScore", {}).get("current", "?")
    texto = f"\n  {home} {gh} - {ga} {away}\n"
    claves_interes = [
        # ── Globales ──────────────────────────────────────────────────────
        "ALL_Ball possession", "ALL_Total shots",  "ALL_Shots on target",
        "ALL_Corner kicks",    "ALL_Yellow cards",  "ALL_Red cards",
        "ALL_Fouls",           "ALL_Big chances",   "ALL_Expected goals",
        "ALL_Total saves",
        # ── Primer tiempo (1ST) ───────────────────────────────────────────
        "1ST_Corner kicks",   "1ST_Yellow cards",  "1ST_Red cards",
        "1ST_Total shots",    "1ST_Shots on target","1ST_Fouls",
        # ── Segundo tiempo (2ND) ──────────────────────────────────────────
        "2ND_Corner kicks",   "2ND_Yellow cards",  "2ND_Red cards",
        "2ND_Total shots",    "2ND_Shots on target","2ND_Fouls",
    ]
    for clave in claves_interes:
        if clave in stats:
            texto += f"    {clave}: {stats[clave]['home']} - {stats[clave]['away']}\n"
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

        eventos1 = obtener_partidos_equipo(page, equipo1)
        eventos2 = obtener_partidos_equipo(page, equipo2)
        partidos1 = [formatear_partido(e, obtener_estadisticas(page, e["id"])) for e in eventos1]
        partidos2 = [formatear_partido(e, obtener_estadisticas(page, e["id"])) for e in eventos2]

        # Buscar el próximo partido entre los dos equipos
        # Busca desde ronda_actual - 1 hasta ronda_actual + 6 para cubrir todos los casos
        ahora = datetime.now().timestamp()
        evento_id_proximo = None
        ronda_inicio = max(1, RONDAS_TOTALES - 1)
        for ronda in range(ronda_inicio, RONDAS_TOTALES + 7):
            data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{TEMPORADA_ID}/events/round/{ronda}")
            for evento in data.get("events", []):
                home = evento["homeTeam"]["name"]
                away = evento["awayTeam"]["name"]
                status = evento.get("status", {}).get("type", "")
                start = evento.get("startTimestamp", 0)
                equipo1_match = equipo1.lower() in home.lower() or equipo1.lower() in away.lower()
                equipo2_match = equipo2.lower() in home.lower() or equipo2.lower() in away.lower()
                es_futuro_o_en_curso = status == "inprogress" or (status == "notstarted" and start > ahora)
                if equipo1_match and equipo2_match and es_futuro_o_en_curso:
                    evento_id_proximo = evento["id"]
                    break
            if evento_id_proximo:
                break

    finally:
        browser.close()
        playwright.stop()

    contexto = "DATOS REALES DE SOFASCORE:\n"
    contexto += f"=== ÚLTIMOS PARTIDOS DE {equipo1.upper()} ===\n"
    for p in partidos1:
        contexto += p
    contexto += f"\n=== ÚLTIMOS PARTIDOS DE {equipo2.upper()} ===\n"
    for p in partidos2:
        contexto += p
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

Antes de disparar ACTION:ANALIZAR, SIEMPRE tenés que tener claro de qué partido específico se habla. Para eso:

CASO A — El usuario menciona un equipo y ese equipo tiene UN SOLO partido próximo:
→ Antes de analizar, confirmás: "Entiendo que hablás del partido contra [rival] el [fecha], ¿es correcto?"
→ Esperás confirmación. Recién ahí disparás ACTION:ANALIZAR.

CASO B — El usuario menciona un equipo y ese equipo tiene MÁS DE UN partido próximo:
→ Preguntás: "¿De qué partido hablás? Tiene [partido1] y [partido2]."
→ Esperás que el usuario especifique. Recién ahí, si pide análisis, disparás ACTION:ANALIZAR.

CASO C — El usuario confirma con "sí", "ese", "correcto" o similar:
→ Solo respondés con la información pendiente (fecha, hora, etc.).
→ NO disparás ACTION:ANALIZAR a menos que el usuario agregue explícitamente un pedido de análisis en ese mismo mensaje.

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
def chat_con_ia(mensaje, datos_sofascore=None, callback=None):
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
            # Así podemos decidir qué hacer ANTES de que el usuario vea algo.
            respuesta = chat_con_ia(mensaje)  # sin callback → no hay streaming

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

                analisis = chat_con_ia(
                f"""Basándote ÚNICAMENTE en los datos de SofaScore que te paso, hacé un análisis orientado a apuestas sobre: {foco}.

                PERÍODO: {instruccion_periodo}

                Calculá SIEMPRE estas métricas usando los datos del período correcto:
                - Resultado más probable (si aplica): comparé victorias, posesión, tiros al arco y goles
                - Over/Under goles: calculá el promedio de goles totales por partido
                - Ambos equipos anotan: en cuántos partidos anotaron ambos
                - Corners: promedio de corners por equipo y total esperado
                - Tarjetas amarillas: promedio por equipo y total esperado
                - Tarjetas rojas: si hubo alguna en los últimos partidos
                - Remates al arco: promedio por equipo
                - Faltas: promedio por equipo
                - Calculá los promedios mostrando la operación: (v1 + v2 + v3) / n = X.
                - Terminá con: "Recomendación: [apuesta concreta con número exacto y período si aplica]"

                Respondé en texto corrido. Sin listas ni bullets. Máximo 160 palabras.
                Terminá con: "⚠️ Solo una recomendación estadística. Los resultados pueden variar."
                No uses conocimiento propio. Solo los datos de DATOS REALES DE SOFASCORE.""",
                datos_sofascore=datos
            )
                

                analisis_limpio = re.sub(r'ACTION:ANALIZAR\|[^\n]+', '', analisis).strip()
                self.agregar_mensaje("🤖", analisis_limpio)
                guardar_prediccion(equipo1, equipo2, foco, analisis_limpio,
                                   evento_id=evento_id, liga_id=LIGA_ID, temporada_id=TEMPORADA_ID)
                self.set_status("✅ Listo")

            # ── Caso B: respuesta normal, mostrar con streaming simulado ──────
            else:
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