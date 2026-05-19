from playwright.sync_api import sync_playwright
import re

def obtener_datos_liga():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        page.goto("https://www.sofascore.com/football/tournament/iceland/besta-deild-karla/188", timeout=30000)
        page.wait_for_timeout(5000)
        
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(3000)
        
        contenido = page.inner_text("body")
        
        with open("debug_sofascore.txt", "w", encoding="utf-8") as f:
            f.write(contenido)
        
        browser.close()
        return contenido

def parsear_tabla(contenido):
    equipos = []
    lineas = contenido.split("\n")
    
    i = 0
    while i < len(lineas):
        linea = lineas[i].strip()
        if re.match(r"^\d+$", linea) and i + 10 < len(lineas):
            try:
                pos = int(linea)
                nombre = lineas[i+1].strip()
                jugados = lineas[i+2].strip()
                ganados = lineas[i+3].strip()
                empates = lineas[i+4].strip()
                perdidos = lineas[i+5].strip()
                diff = lineas[i+6].strip()
                goles = lineas[i+7].strip()
                puntos = lineas[i+13].strip() if i+13 < len(lineas) else "?"
                
                if nombre and jugados.isdigit():
                    equipos.append({
                        "pos": pos,
                        "nombre": nombre,
                        "jugados": jugados,
                        "ganados": ganados,
                        "empates": empates,
                        "perdidos": perdidos,
                        "diff": diff,
                        "goles": goles,
                        "puntos": puntos
                    })
                    i += 14
                    continue
            except:
                pass
        i += 1
    
    return equipos

def parsear_resultados_recientes(contenido):
    resultados = []
    lineas = contenido.split("\n")
    
    i = 0
    while i < len(lineas):
        if lineas[i].strip() == "FT":
            try:
                fecha = lineas[i-1].strip()
                local = lineas[i+1].strip()
                visitante = lineas[i+2].strip()
                gol_local = lineas[i+3].strip()
                gol_visit = lineas[i+5].strip()
                
                if gol_local.isdigit() and gol_visit.isdigit() and local and visitante:
                    resultados.append({
                        "fecha": fecha,
                        "local": local,
                        "goles_local": int(gol_local),
                        "goles_visitante": int(gol_visit),
                        "visitante": visitante
                    })
            except:
                pass
        i += 1
    
    return resultados

def mostrar_tabla(equipos):
    print("\n=== TABLA ACTUAL - BESTA DEILD KARLA 2026 ===")
    print(f"{'Pos':<4} {'Equipo':<20} {'PJ':<4} {'G':<4} {'E':<4} {'P':<4} {'DIF':<6} {'Goles':<8} {'Pts':<4}")
    print("-" * 60)
    for e in equipos:
        print(f"{e['pos']:<4} {e['nombre']:<20} {e['jugados']:<4} {e['ganados']:<4} {e['empates']:<4} {e['perdidos']:<4} {e['diff']:<6} {e['goles']:<8} {e['puntos']:<4}")

def mostrar_resultados(resultados):
    print("\n=== ÚLTIMOS RESULTADOS ===")
    if not resultados:
        print("  Sin resultados encontrados")
    for r in resultados:
        print(f"  {r['local']} {r['goles_local']} - {r['goles_visitante']} {r['visitante']} ({r['fecha']})")

print("🔄 Obteniendo datos de SofaScore...")
contenido = obtener_datos_liga()

tabla = parsear_tabla(contenido)
resultados = parsear_resultados_recientes(contenido)

mostrar_tabla(tabla)
mostrar_resultados(resultados)