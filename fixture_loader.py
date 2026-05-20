from playwright.sync_api import sync_playwright
from datetime import datetime

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

def fetch_api(page, url):
    return page.evaluate(f"""
        async () => {{
            const r = await fetch('{url}');
            return await r.json();
        }}
    """)

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

        for nombre_liga, datos in LIGAS.items():
            try:
                rounds_data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{datos['id']}/season/{datos['temporada']}/rounds")
                rondas = rounds_data.get("rounds", [])
                ronda_actual = rondas[-1].get("round", datos["rondas"]) if rondas else datos["rondas"]

                eventos = []
                for r in [ronda_actual, ronda_actual - 1]:
                    data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{datos['id']}/season/{datos['temporada']}/events/round/{r}")
                    todos = data.get("events", [])
                    proximos = [
                        e for e in todos
                        if e.get("status", {}).get("type") == "notstarted"
                        and e.get("startTimestamp", 0) > ahora
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