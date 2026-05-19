from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    
    try:
        page.goto("https://www.sofascore.com/football/tournament/iceland/besta-deild-karla/188", timeout=15000)
        page.wait_for_timeout(8000)
        
        contenido = page.inner_text("body")
        
        # Guardar el contenido completo para analizarlo
        with open("sofascore_raw.txt", "w", encoding="utf-8") as f:
            f.write(contenido)
        
        print("✅ Contenido guardado en sofascore_raw.txt")
        print("\nPrimeras 3000 caracteres:")
        print(contenido[:3000])
        
    except Exception as e:
        print("Error:", e)
    finally:
        browser.close()