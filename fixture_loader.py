from playwright.sync_api import sync_playwright
from datetime import datetime

LIGAS_CONFIG = {
    "Besta deild karla": 188,
    "La Liga": 8,
    "Premier League": 17,
    "Serie A": 23,
    "Bundesliga": 35,
    "Ligue 1": 34,
    "Champions League": 7,
    "Liga Argentina": 406,
    "Copa Libertadores": 384,
    "Copa Sudamericana": 480,
    "Saudi Pro League": 955,
}
LIGAS = {}
def fetch_api(page, url):
    return page.evaluate(f"""
        async () => {{
            const r = await fetch('{url}');
            return await r.json();
        }}
    """)

def obtener_temporadas_actuales(page):
    ligas = {}
    for nombre, liga_id in LIGAS_CONFIG.items():
        try:
            data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/seasons")
            temporadas = data.get("seasons", [])
            if temporadas:
                temporada_id = temporadas[0]["id"]
                # Obtener la ronda actual real de la API
                rounds_data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/season/{temporada_id}/rounds")
                rondas = rounds_data.get("rounds", [])
                # currentRound.round = ronda en curso; fallback a última ronda del array
                ronda_actual = (
                    rounds_data.get("currentRound", {}).get("round")
                    or (rondas[-1].get("round", 1) if rondas else 1)
                )
                ligas[nombre] = {
                    "id": liga_id,
                    "temporada": temporada_id,
                    "rondas": ronda_actual  # ronda real, no hardcodeada
                }
        except:
            pass
    global LIGAS
    LIGAS = ligas
    return ligas

def cargar_proximos_partidos():
    contexto = "=== PRÓXIMOS PARTIDOS POR LIGA ===\n"
    ahora = datetime.now().timestamp()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto("https://www.sofascore.com", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        global LIGAS
        LIGAS = obtener_temporadas_actuales(page)

        for nombre_liga, datos in LIGAS.items():
            try:
                # La ronda actual ya viene calculada desde obtener_temporadas_actuales
                ronda_actual = datos["rondas"]

                eventos = []
                # Buscar en ronda actual y siguiente (hacia adelante, no hacia atrás)
                for r in [ronda_actual, ronda_actual + 1]:
                    data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{datos['id']}/season/{datos['temporada']}/events/round/{r}")
                    todos = data.get("events", [])
                    proximos = [
                        e for e in todos
                        if e.get("status", {}).get("type") == "inprogress"
                        or (
                            e.get("status", {}).get("type") == "notstarted"
                            and e.get("startTimestamp", 0) > ahora
                        )
                    ]
                    if proximos:
                        eventos = proximos
                        break

                if eventos:
                    contexto += f"\n{nombre_liga}:\n"
                    for e in eventos:
                        home = e["homeTeam"]["name"]
                        away = e["awayTeam"]["name"]
                        fecha = e.get("startTimestamp", "")
                        fecha_str = datetime.fromtimestamp(fecha).strftime("%d/%m/%Y %H:%M") if fecha else "por confirmar"
                        contexto += f"  - {home} vs {away} ({fecha_str})\n"
            except:
                pass

        browser.close()

    return contexto