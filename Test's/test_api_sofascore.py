from playwright.sync_api import sync_playwright

def fetch_api(page, url):
    return page.evaluate(f"""
        async () => {{
            const r = await fetch('{url}');
            return await r.json();
        }}
    """)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    page.goto("https://www.sofascore.com", timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    ligas = {
        "La Liga": 8,
        "Premier League": 17,
        "Serie A": 23,
        "Bundesliga": 35,
        "Ligue 1": 34,
        "Champions League": 7,
        "Liga Argentina": 406
    }

    for nombre, liga_id in ligas.items():
        data = fetch_api(page, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/seasons")
        temporadas = data.get("seasons", [])
        if temporadas:
            ultima = temporadas[0]
            print(f"{nombre}: ID liga={liga_id} | Temporada={ultima['name']} | ID temporada={ultima['id']}")

    browser.close()