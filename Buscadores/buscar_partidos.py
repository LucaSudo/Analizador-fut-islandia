from playwright.sync_api import sync_playwright
import json

def obtener_api_calls():
    llamadas = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        # Interceptar requests de red
        def capturar_request(request):
            if "api.sofascore.com" in request.url:
                llamadas.append(request.url)
        
        page.on("request", capturar_request)
        
        page.goto("https://www.sofascore.com/football/tournament/iceland/besta-deild-karla/188",
                  timeout=30000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        
        browser.close()
    
    return llamadas

calls = obtener_api_calls()
print(f"API calls encontradas: {len(calls)}")
for c in calls[:15]:
    print(c)