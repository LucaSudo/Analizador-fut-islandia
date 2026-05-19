import json

with open("datos/partidos_islandia_2024.json", "r", encoding="utf-8") as f:
    data = json.load(f)

partidos = data["response"]

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
                "rival": away,
                "goles_favor": gh,
                "goles_contra": ga,
                "localia": "local",
                "resultado": "G" if gh > ga else ("E" if gh == ga else "P")
            })
        elif away == equipo:
            resultado.append({
                "rival": home,
                "goles_favor": ga,
                "goles_contra": gh,
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
                "local": home,
                "visitante": away,
                "resultado": f"{gh}-{ga}",
                "ganador": ganador
            })
    return h2h

def armar_contexto(equipo_local, equipo_visitante):
    ult_local = get_ultimos_partidos(equipo_local)
    ult_visit = get_ultimos_partidos(equipo_visitante)
    h2h = get_head_to_head(equipo_local, equipo_visitante)
    
    def racha(partidos):
        return " ".join([p["resultado"] for p in partidos])
    
    def goles_stats(partidos):
        if not partidos:
            return "Sin datos"
        gf = sum(p["goles_favor"] for p in partidos)
        gc = sum(p["goles_contra"] for p in partidos)
        return f"GF: {gf} | GC: {gc} en {len(partidos)} partidos"
    
    contexto = f"""
=== ANÁLISIS: {equipo_local} vs {equipo_visitante} ===

{equipo_local} (LOCAL):
  Últimos {len(ult_local)} partidos: {racha(ult_local)}
  {goles_stats(ult_local)}

{equipo_visitante} (VISITANTE):
  Últimos {len(ult_visit)} partidos: {racha(ult_visit)}
  {goles_stats(ult_visit)}

HEAD TO HEAD ({len(h2h)} enfrentamientos en 2024):
"""
    for p in h2h:
        contexto += f"  {p['local']} {p['resultado']} {p['visitante']} → Ganó: {p['ganador']}\n"
    
    if not h2h:
        contexto += "  Sin enfrentamientos directos en 2024\n"
    
    return contexto

# Probar con un partido
contexto = armar_contexto("Breidablik", "Valur Reykjavik")
print(contexto)