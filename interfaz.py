from dotenv import load_dotenv
import os

load_dotenv()
import customtkinter as ctk
import threading
import asyncio
import edge_tts
from playsound import playsound
from groq import Groq
from playwright.sync_api import sync_playwright
import json
import os
from memory import cargar_memoria, guardar_prediccion, generar_contexto_memoria

API_KEY_GROQ = os.getenv("GROQ_API_KEY")
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

    contexto = f"DATOS REALES DE SOFASCORE:\n"
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

SYSTEM_PROMPT = """Sos una persona que sabe de fútbol, especialmente de la liga islandesa Besta deild karla en general.
Hablás como un amigo entendido, no como un robot ni un empleado. Usás lenguaje rioplatense natural, no exagerado con los ademanes porque puede ser agotador, solo cada cierto tiempo. No seas sugerente ni invasivo, no estes queriendo redireccionar la conversación todo el tiempo, solo respondé lo que te pregunten. Si no sabés algo, decilo sin problema, no intentes inventar nada. No sos un modelo de lenguaje, sos una persona que sabe de futbol y que con el tiempo sabra mas.
Conocés todos los equipos: Breidablik Kópavogur, Valur Reykjavík, Víkingur Reykjavík, KR Reykjavík, Fram Reykjavík, 
KA Akureyri, Stjarnan Garðabær, Keflavík IF, ÍA Akranes, FH Hafnarfjörður, Þór Akureyri, ÍBV Vestmannaeyjar.

REGLAS IMPORTANTES:
- Si el usuario menciona un partido o pide una predicción, PRIMERO confirmá los equipos y qué quiere analizar antes de hacer nada.
  Ejemplo: "Che, ¿querés que mire Breidablik vs KR Reykjavík enfocado en corners?"
- - Cuando confirmés los equipos, SOLO hacé la pregunta de confirmación. NO incluyas ACTION:ANALIZAR en ese mensaje bajo ninguna circunstancia.
- ACTION:ANALIZAR solo va en el mensaje SIGUIENTE, cuando el usuario ya respondió "sí", "dale", "sí eso" o similar.
- Si el usuario no confirmó todavía, jamás pongas ACTION:ANALIZAR.
- Si el usuario confirma y ya tenés los datos, usá exactamente este formato al final de tu respuesta:
  ACTION:ANALIZAR|equipo1|equipo2|foco
  Donde foco puede ser: corners, goles, tarjetas, completo
- Si el usuario pregunta algo general de fútbol, respondé directo sin pedir confirmación.
- Nunca hagas análisis sin confirmar primero.
- Sé conciso, no escribas parrafotes."""

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
            if callback:
                callback(delta)

    historial.append({"role": "assistant", "content": respuesta_completa})
    return respuesta_completa
callback = None

# ── Interfaz ─────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("⚽ Chat Besta deild karla")
        self.geometry("750x650")
        self.resizable(True, True)
        self.bind("<F11>", lambda e: self.attributes("-fullscreen", not self.attributes("-fullscreen")))
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        self.esperando_analisis = None

        ctk.CTkLabel(self, text="⚽ Chat Besta deild karla 2026",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=15)

        self.chat = ctk.CTkTextbox(self, height=450, font=ctk.CTkFont(size=13), wrap="word")
        self.chat.pack(pady=5, padx=20, fill="both", expand=True)
        self.chat.configure(state="disabled")

        self.label_status = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.label_status.pack()

        frame_input = ctk.CTkFrame(self, fg_color="transparent")
        frame_input.pack(pady=10, padx=20, fill="x")

        self.input = ctk.CTkTextbox(frame_input, height=40, font=ctk.CTkFont(size=13))
        self.input.bind("<Return>", lambda e: self.enviar() or "break")
        self.input.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.input.bind("<Return>", lambda e: self.enviar())

        self.btn_enviar = ctk.CTkButton(frame_input, text="Enviar", width=90, height=40,
                                         command=self.enviar)
        self.btn_enviar.pack(side="right")

        self.agregar_mensaje("🤖", "¡Buenas! ¿De qué hablamos? Puedo charlar de la Besta deild, darte datos, o analizar un partido si me decís cuál.")

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
        self.input.delete("1.0", "end")
        self.agregar_mensaje("👤", mensaje)
        self.btn_enviar.configure(state="disabled")
        self.input.configure(state="disabled")
        thread = threading.Thread(target=self.procesar, args=(mensaje,))
        thread.daemon = True
        thread.start()

    def procesar(self, mensaje):
        try:
            respuesta = chat_con_ia(mensaje)

            # Detectar si la IA quiere hacer un análisis
            if "ACTION:ANALIZAR|" in respuesta:
                partes = respuesta.split("ACTION:ANALIZAR|")
                texto_visible = partes[0].strip()
                datos_action = partes[1].strip().split("|")
                
                equipo1 = datos_action[0]
                equipo2 = datos_action[1]
                foco = datos_action[2] if len(datos_action) > 2 else "completo"

                if texto_visible:
                    self.agregar_mensaje("🤖", texto_visible)

                self.set_status(f"🔄 Bajando datos de SofaScore...")
                datos = hacer_analisis_completo(equipo1, equipo2)

                self.set_status("🤖 Analizando...")
                analisis = chat_con_ia(
                    f"Ahora sí, hacé el análisis enfocado en: {foco}. Sé conciso y directo.",
                    datos_sofascore=datos
                )

                # Limpiar posible ACTION del análisis final
                if "ACTION:" in analisis:
                    analisis = analisis.split("ACTION:")[0].strip()

                self.agregar_mensaje("🤖", analisis)
                guardar_prediccion(equipo1, equipo2, foco, analisis)

                self.set_status("🔊 Generando audio...")
                asyncio.run(generar_audio(analisis))
                playsound("analisis_final.mp3")
                self.set_status("✅ Listo")

            else:
                self.agregar_mensaje("🤖", "")
                def stream_callback(texto):
                    self.chat.configure(state="normal")
                    self.chat.insert("end", texto)
                    self.chat.see("end")
                    self.chat.configure(state="disabled")
                    self.update()

                respuesta = chat_con_ia(mensaje, callback=stream_callback)

        except Exception as e:
            self.agregar_mensaje("❌ Error", str(e))
            self.set_status("")
        finally:
            self.btn_enviar.configure(state="normal")
            self.input.configure(state="normal")

if __name__ == "__main__":
    app = App()
    app.mainloop()