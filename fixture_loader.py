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
                # SofaScore tiene dos endpoints complementarios:
                #   last/0  → última ronda (puede tener partidos AÚN NO JUGADOS
                #              si la ronda está en curso o parcialmente jugada)
                #   next/0  → próxima ronda completa
                # Es necesario consultar AMBOS para no perder partidos de la
                # ronda actual que todavía no se jugaron (caso Valur vs KR).
                candidatos = []
                base = (f"https://www.sofascore.com/api/v1/unique-tournament"
                        f"/{datos['id']}/season/{datos['temporada']}/events")
                for endpoint in ["last/0", "next/0"]:
                    try:
                        resp = fetch_api(page, f"{base}/{endpoint}")
                        candidatos.extend(resp.get("events", []))
                    except:
                        pass

                # Filtrar: solo futuros o en curso, deduplicar por id, ordenar
                vistos = set()
                eventos = []
                for e in sorted(candidatos, key=lambda x: x.get("startTimestamp", 0)):
                    eid = e["id"]
                    tipo = e.get("status", {}).get("type", "")
                    ts   = e.get("startTimestamp", 0)
                    es_futuro = tipo == "inprogress" or (tipo == "notstarted" and ts > ahora)
                    if es_futuro and eid not in vistos:
                        vistos.add(eid)
                        eventos.append(e)

                if eventos:
                    contexto += f"\n{nombre_liga}:\n"
                    for e in eventos:
                        home = e["homeTeam"]["name"]
                        away = e["awayTeam"]["name"]
                        ts   = e.get("startTimestamp", "")
                        fecha_str = (
                            datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
                            if ts else "por confirmar"
                        )
                        contexto += f"  - {home} vs {away} ({fecha_str})\n"
            except:
                pass

        browser.close()

    return contexto