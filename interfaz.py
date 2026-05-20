from dotenv import load_dotenv
load_dotenv()
from fixture_loader import cargar_proximos_partidos
import os
import re
import asyncio
import threading
import json

import customtkinter as ctk
import edge_tts
from playsound import playsound
from groq import Groq
from playwright.sync_api import sync_playwright
from memory import cargar_memoria, guardar_prediccion, generar_contexto_memoria

# ── Configuración ────────────────────────────────────────────────

API_KEY_GROQ = os.getenv("GROQ_API_KEY")

LIGAS = {
    "Besta deild karla": {"id": 188, "temporada": 89094, "rondas": 7},
    "La Liga":           {"id": 8,   "temporada": 77559, "rondas": 34},
    "Premier League":    {"id": 17,  "temporada": 76986, "rondas": 36},
    "Serie A":           {"id": 23,  "temporada": 76457, "rondas": 34},
    "Bundesliga":        {"id": 35,  "temporada": 77333, "rondas": 34},
    "Ligue 1":           {"id": 34,  "temporada": 77356, "rondas": 34},
    "Champions League":  {"id": 7,   "temporada": 76953, "rondas": 8},
    "Liga Argentina":    {"id": 406, "temporada": 88529, "rondas": 14},
}

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
            if grupo["period"] != "ALL":
                continue
            for g in grupo["groups"]:
                for item in g["statisticsItems"]:
                    stats[item["name"]] = {
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
        "Ball possession", "Total shots", "Shots on target", "Corner kicks",
        "Yellow cards", "Red cards", "Big chances", "Expected goals",
        "Total saves", "Fouls"
    ]
    for clave in claves_interes:
        if clave in stats:
            texto += f"    {clave}: {stats[clave]['home']} - {stats[clave]['away']}\n"
    return texto

def hacer_analisis_completo(equipo1, equipo2):
    playwright, browser, page = obtener_pagina()
    try:
        eventos1 = obtener_partidos_equipo(page, equipo1)
        eventos2 = obtener_partidos_equipo(page, equipo2)
        partidos1 = [formatear_partido(e, obtener_estadisticas(page, e["id"])) for e in eventos1]
        partidos2 = [formatear_partido(e, obtener_estadisticas(page, e["id"])) for e in eventos2]
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
    return contexto

async def generar_audio(texto):
    communicate = edge_tts.Communicate(texto, voice="es-AR-TomasNeural")
    await communicate.save("analisis_final.mp3")

# ── Chat con IA ──────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos una persona que sabe de fútbol, especialmente de la liga islandesa Besta deild karla y otras ligas europeas y argentinas.
Hablás como un amigo entendido, no como un robot ni un empleado. Usás lenguaje argentino natural, no exagerado.
No seas sugerente ni invasivo, no estés redirigiendo la conversación todo el tiempo. Solo respondé lo que te pregunten.
Si no sabés algo, decilo sin problema. No inventes nada.

Tenés acceso a datos en tiempo real de estas ligas:
- Besta deild karla (Islandia)
- La Liga (España)
- Premier League (Inglaterra)
- Serie A (Italia)
- Bundesliga (Alemania)
- Ligue 1 (Francia)
- Champions League
- Liga Argentina

Cuando el usuario pida un análisis, identificá la liga automáticamente según los equipos o el contexto.
Si tenés dudas de la liga, preguntá.

REGLAS IMPORTANTES:
- Si el usuario pide un análisis con equipos y liga claros, ejecutá directamente con ACTION:ANALIZAR.
- Solo pedí confirmación si los equipos o la liga son ambiguos o no quedaron claros.
- ACTION:ANALIZAR siempre va AL FINAL del mensaje, nunca visible para el usuario."""

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
        temperature=0.85,
        max_tokens=400,
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
                    # Hold back up to 6 chars that could be a partial "ACTION:" prefix
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
            self.agregar_mensaje("🤖", "")

            def stream_callback(texto):
                self.chat.configure(state="normal")
                self.chat.insert("end", texto)
                self.chat.see("end")
                self.chat.configure(state="disabled")
                self.update()

            respuesta = chat_con_ia(mensaje, callback=stream_callback)

            if "ACTION:ANALIZAR|" in respuesta:
                match = re.search(r'ACTION:ANALIZAR\|(.*?)\|(.*?)\|(.*?)\|(.*?)(?:\n|$)', respuesta)
                if match:
                    equipo1 = match.group(1).strip()
                    equipo2 = match.group(2).strip()
                    foco = match.group(3).strip()
                    liga_nombre = match.group(4).strip()
                else:
                    partes = respuesta.split("ACTION:ANALIZAR|")[1].split("|")
                    equipo1 = partes[0].strip()
                    equipo2 = partes[1].strip()
                    foco = partes[2].strip() if len(partes) > 2 else "completo"
                    liga_nombre = partes[3].strip() if len(partes) > 3 else "Besta deild karla"

                liga = next((v for k, v in LIGAS.items() if liga_nombre in k), LIGAS["Besta deild karla"])
                LIGA_ID = liga["id"]
                TEMPORADA_ID = liga["temporada"]
                RONDAS_TOTALES = liga["rondas"]

                self.set_status("🔄 Bajando datos de SofaScore...")
                datos = hacer_analisis_completo(equipo1, equipo2)

                self.set_status("🤖 Analizando...")
                analisis = chat_con_ia(
                    f"Ahora sí, hacé el análisis enfocado en: {foco}. Sé conciso y directo.",
                    datos_sofascore=datos
                )

                analisis_limpio = re.sub(r'ACTION:ANALIZAR\|[^\n]+', '', analisis).strip()
                self.agregar_mensaje("🤖", analisis_limpio)
                guardar_prediccion(equipo1, equipo2, foco, analisis_limpio)

                self.set_status("🔊 Generando audio...")
                asyncio.run(generar_audio(analisis_limpio))
                playsound("analisis_final.mp3")
                self.set_status("✅ Listo")
            else:
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
    print("🔄 Cargando fixtures...")
    fixtures = cargar_proximos_partidos()
    print(fixtures)
    print("✅ Fixtures cargados")
    app = App(fixtures)
    app.mainloop()