from dotenv import load_dotenv
import os
load_dotenv()
from groq import Groq
import json

# Cargar partidos
with open("datos/partidos_islandia_2024.json", "r", encoding="utf-8") as f:
    data = json.load(f)

partidos = data["response"]

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def get_ultimos_partidos(equipo, n=5):
    resultado = []
    for p in partidos:
        home = p["teams"]["home"]["name"]
        away = p["teams"]["away"]["name"]
        gh = p["goals"]["home"]
        ga = p["goals"]["away"]
        if gh is None or ga is None:
            continue
        if home == equipo:
            resultado.append({
                "rival": away, "goles_favor": gh, "goles_contra": ga,
                "localia": "local",
                "resultado": "G" if gh > ga else ("E" if gh == ga else "P")
            })
        elif away == equipo:
            resultado.append({
                "rival": home, "goles_favor": ga, "goles_contra": gh,
                "localia": "visitante",
                "resultado": "G" if ga > gh else ("E" if gh == ga else "P")
            })
    return resultado[-n:]

def get_head_to_head(equipo1, equipo2):
    h2h = []
    for p in partidos:
        home = p["teams"]["home"]["name"]
        away = p["teams"]["away"]["name"]
        gh = p["goals"]["home"]
        ga = p["goals"]["away"]
        if gh is None or ga is None:
            continue
        if (home == equipo1 and away == equipo2) or (home == equipo2 and away == equipo1):
            ganador = home if gh > ga else (away if ga > gh else "Empate")
            h2h.append({
                "local": home, "visitante": away,
                "resultado": f"{gh}-{ga}", "ganador": ganador
            })
    return h2h

def armar_contexto(equipo_local, equipo_visitante):
    ult_local = get_ultimos_partidos(equipo_local)
    ult_visit = get_ultimos_partidos(equipo_visitante)
    h2h = get_head_to_head(equipo_local, equipo_visitante)

    def racha(ps):
        return " ".join([p["resultado"] for p in ps])

    def goles_stats(ps):
        if not ps:
            return "Sin datos"
        gf = sum(p["goles_favor"] for p in ps)
        gc = sum(p["goles_contra"] for p in ps)
        return f"GF: {gf} | GC: {gc} en {len(ps)} partidos"

    contexto = f"""
Partido a analizar: {equipo_local} (local) vs {equipo_visitante} (visitante)
Liga: Úrvalsdeild 2024 - Islandia

{equipo_local} - últimos {len(ult_local)} partidos:
  Racha: {racha(ult_local)}
  {goles_stats(ult_local)}

{equipo_visitante} - últimos {len(ult_visit)} partidos:
  Racha: {racha(ult_visit)}
  {goles_stats(ult_visit)}

Enfrentamientos directos en 2024 ({len(h2h)} partidos):
"""
    for p in h2h:
        contexto += f"  {p['local']} {p['resultado']} {p['visitante']} → Ganó: {p['ganador']}\n"

    if not h2h:
        contexto += "  Sin enfrentamientos directos registrados\n"

    return contexto

def analizar(equipo_local, equipo_visitante):
    contexto = armar_contexto(equipo_local, equipo_visitante)

    print(f"\n📊 Analizando: {equipo_local} vs {equipo_visitante}...\n")

    respuesta = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": """Sos un analista de fútbol experto y minucioso. 
Tu trabajo es analizar datos estadísticos de partidos y producir un análisis detallado y justificado.
Siempre basás tus conclusiones en los datos provistos.
Identificás patrones, fortalezas, debilidades y factores de incertidumbre.
Respondés en español."""
            },
            {
                "role": "user",
                "content": f"""Analizá este partido en profundidad basándote en los siguientes datos:

{contexto}

Por favor:
1. Analizá la forma reciente de cada equipo
2. Interpretá el historial de enfrentamientos directos
3. Identificá ventajas y desventajas de cada equipo
4. Señalá factores de incertidumbre
5. Dá tu conclusión final justificada"""
            }
        ],
        temperature=0.7
    )

    return respuesta.choices[0].message.content

# Ejecutar análisis
resultado = analizar("Breidablik", "Valur Reykjavik")
print(resultado)