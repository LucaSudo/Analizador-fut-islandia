import os
from playwright.sync_api import sync_playwright
import json
import asyncio
import edge_tts
from groq import Groq

API_KEY_GROQ = "os.getenv("GROQ_API_KEY")"
LIGA_ID = 188
TEMPORADA_ID = 89094
RONDAS_TOTALES = 7  # actualizar según avance la liga

client = Groq(api_key=API_KEY_GROQ)

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
    fecha = evento.get("startTimestamp", "?")
    
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

def armar_contexto(equipo_local, equipo_visitante, partidos_local, partidos_visitante):
    contexto = f"PARTIDO A ANALIZAR: {equipo_local} vs {equipo_visitante}\n"
    contexto += f"Liga: Besta deild karla 2026 - Islandia\n\n"
    
    contexto += f"=== ÚLTIMOS PARTIDOS DE {equipo_local.upper()} ===\n"
    for p in partidos_local:
        contexto += p
    
    contexto += f"\n=== ÚLTIMOS PARTIDOS DE {equipo_visitante.upper()} ===\n"
    for p in partidos_visitante:
        contexto += p
    
    return contexto

def analizar_con_ia(contexto):
    respuesta = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": """Sos un analista de fútbol experto y minucioso.
Analizás estadísticas reales de partidos y producís análisis detallados y justificados.
Siempre basás tus conclusiones en los datos provistos.
Predecís: ganador probable, cantidad estimada de goles, corners, tarjetas amarillas.
Justificás cada predicción con los datos.
Respondés en español, de forma clara y estructurada."""
            },
            {
    "role": "user",
    "content": f"""Analizá este partido y respondé ÚNICAMENTE en este formato exacto, sin agregar texto extra:

{contexto}

FORMATO DE RESPUESTA:
[Equipo local] vs [Equipo visitante]

Ganador probable: [equipo] — [justificación en máximo 10 palabras]
Local/visitante: determinalo vos segun el historial de cada equipo
Resultado más probable: [X-X]
Goles totales: [número] — [justificación en máximo 10 palabras]
Corners totales: [número] — [justificación en máximo 10 palabras]
Tarjetas amarillas: [número] — [justificación en máximo 10 palabras]
Nivel de incertidumbre: [bajo/medio/alto] — [justificación en máximo 10 palabras]"""
}
        ],
        temperature=0.7
    )
    return respuesta.choices[0].message.content

async def hablar(texto):
    communicate = edge_tts.Communicate(texto, voice="es-AR-TomasNeural")
    await communicate.save("analisis_final.mp3")
    print("✅ Audio guardado en analisis_final.mp3")

def analizar_partido(equipo_local, equipo_visitante):
    print(f"\n🔄 Obteniendo datos de SofaScore...")
    playwright, browser, page = obtener_pagina()
    
    try:
        print(f"📊 Buscando partidos de {equipo_local}...")
        eventos_local = obtener_partidos_equipo(page, equipo_local)
        
        print(f"📊 Buscando partidos de {equipo_visitante}...")
        eventos_visitante = obtener_partidos_equipo(page, equipo_visitante)
        
        print(f"📈 Obteniendo estadísticas detalladas...")
        
        partidos_local = []
        for evento in eventos_local:
            stats = obtener_estadisticas(page, evento["id"])
            partidos_local.append(formatear_partido(evento, stats))
        
        partidos_visitante = []
        for evento in eventos_visitante:
            stats = obtener_estadisticas(page, evento["id"])
            partidos_visitante.append(formatear_partido(evento, stats))
        
    finally:
        browser.close()
        playwright.stop()
    
    contexto = armar_contexto(equipo_local, equipo_visitante, partidos_local, partidos_visitante)
    
    print("\n=== CONTEXTO GENERADO ===")
    print(contexto)
    
    print("\n🤖 Analizando con IA...")
    analisis = analizar_con_ia(contexto)
    
    print("\n=== ANÁLISIS ===")
    print(analisis)
    
    print("\n🔊 Generando audio...")
    asyncio.run(hablar(analisis))

# --- EJECUTAR ---
print("\n=== ANALIZADOR DE PARTIDOS - BESTA DEILD KARLA ===")
equipo1 = input("Equipo 1: ")
equipo2 = input("Equipo 2: ")
analizar_partido(equipo1, equipo2)