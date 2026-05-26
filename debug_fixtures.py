"""
Script de diagnóstico: muestra exactamente qué devuelve SofaScore
para la Besta deild karla. Correr desde la terminal:
    python debug_fixtures.py
"""
from playwright.sync_api import sync_playwright
from datetime import datetime

LIGA_ID = 188  # Besta deild karla

def fetch(page, url):
    return page.evaluate(f"""
        async () => {{
            const r = await fetch('{url}');
            return await r.json();
        }}
    """)

def ts(t):
    return datetime.fromtimestamp(t).strftime("%d/%m/%Y %H:%M") if t else "?"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent="Mozilla/5.0")
    page = ctx.new_page()
    page.goto("https://www.sofascore.com", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    # 1) Temporada actual
    seasons = fetch(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/seasons")
    temporada_id = seasons["seasons"][0]["id"]
    print(f"Temporada actual: {temporada_id}\n")

    # 2) Ronda actual
    rounds = fetch(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{temporada_id}/rounds")
    ronda = rounds.get("currentRound", {}).get("round", "?")
    print(f"currentRound: {ronda}\n")

    # 3) last/0
    print("=== events/last/0 ===")
    last = fetch(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{temporada_id}/events/last/0")
    ahora = datetime.now().timestamp()
    for e in last.get("events", []):
        home = e["homeTeam"]["name"]
        away = e["awayTeam"]["name"]
        t    = e.get("startTimestamp", 0)
        status = e.get("status", {}).get("type", "?")
        futuro = "⬅ FUTURO" if t > ahora else ""
        print(f"  {ts(t)} | {status:12} | {home} vs {away} {futuro}")

    # 4) next/0
    print("\n=== events/next/0 ===")
    nxt = fetch(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{temporada_id}/events/next/0")
    for e in nxt.get("events", []):
        home = e["homeTeam"]["name"]
        away = e["awayTeam"]["name"]
        t    = e.get("startTimestamp", 0)
        status = e.get("status", {}).get("type", "?")
        print(f"  {ts(t)} | {status:12} | {home} vs {away}")

    # 5) Buscar Valur específicamente en todas las rondas recientes
    print(f"\n=== Buscando 'Valur' en rondas {max(1,ronda-2) if isinstance(ronda,int) else 1}–{(ronda+3) if isinstance(ronda,int) else 12} ===")
    if isinstance(ronda, int):
        for r in range(max(1, ronda-2), ronda+4):
            data = fetch(page, f"https://www.sofascore.com/api/v1/unique-tournament/{LIGA_ID}/season/{temporada_id}/events/round/{r}")
            for e in data.get("events", []):
                home = e["homeTeam"]["name"]
                away = e["awayTeam"]["name"]
                if "valur" in home.lower() or "valur" in away.lower():
                    t = e.get("startTimestamp", 0)
                    status = e.get("status", {}).get("type", "?")
                    futuro = "⬅ FUTURO" if t > ahora else ""
                    print(f"  Ronda {r} | {ts(t)} | {status:12} | {home} vs {away} {futuro}")

    browser.close()
    print("\nDiagnóstico terminado.")
