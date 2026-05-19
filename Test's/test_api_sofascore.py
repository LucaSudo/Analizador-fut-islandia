from playwright.sync_api import sync_playwright
import json

def fetch_api(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto("https://www.sofascore.com", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        
        respuesta = page.evaluate(f"""
            async () => {{
                const r = await fetch('{url}');
                return await r.json();
            }}
        """)
        
        browser.close()
        return respuesta

print("🔄 Obteniendo partidos de la ronda 7...")
data = fetch_api("https://www.sofascore.com/api/v1/unique-tournament/188/season/89094/events/round/7")

with open("ronda7.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print("✅ Guardado en ronda7.json")

# Mostrar partidos
for evento in data.get("events", []):
    home = evento["homeTeam"]["name"]
    away = evento["awayTeam"]["name"]
    gh = evento.get("homeScore", {}).get("current", "?")
    ga = evento.get("awayScore", {}).get("current", "?")
    print(f"  {home} {gh} - {ga} {away}")

    # Mostrar IDs de cada partido
print("\nIDs de partidos:")
for evento in data.get("events", []):
    home = evento["homeTeam"]["name"]
    away = evento["awayTeam"]["name"]
    id_partido = evento["id"]
    print(f"  {id_partido} → {home} vs {away}")

    print("\n🔄 Obteniendo estadísticas de Valur vs Breidablik...")
stats = fetch_api("https://www.sofascore.com/api/v1/event/15426826/statistics")

with open("stats_partido.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, indent=2, ensure_ascii=False)

print("✅ Guardado en stats_partido.json")

# Mostrar estadísticas
for grupo in stats.get("statistics", []):
    print(f"\n{grupo['period']}")
    for stat in grupo["groups"]:
        print(f"  {stat['groupName']}")
        for item in stat["statisticsItems"]:
            print(f"    {item['name']}: {item['home']} - {item['away']}")